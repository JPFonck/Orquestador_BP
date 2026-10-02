#!/usr/bin/env bash
# Elimina el servicio (y su balanceador) para dejar de pagar. Con --all borra también
# el repositorio ECR, el secreto, los logs, el cluster y los roles IAM.
source "$(dirname "$0")/config.sh"

SERVICE_ARN="$(aws ecs describe-services --cluster "$CLUSTER" --services "$APP_NAME" \
  --query "services[?status=='ACTIVE'].serviceArn | [0]" --output text 2>/dev/null || true)"
if [ -n "$SERVICE_ARN" ] && [ "$SERVICE_ARN" != "None" ]; then
  log "Eliminando servicio Express $APP_NAME (también elimina el balanceador)"
  aws ecs delete-express-gateway-service --service-arn "$SERVICE_ARN" >/dev/null
  aws ecs wait services-inactive --cluster "$CLUSTER" --services "$APP_NAME" || true
fi

if [ "${1:-}" = "--all" ]; then
  log "Eliminando recursos base"
  aws ecr delete-repository --repository-name "$APP_NAME" --force >/dev/null 2>&1 || true
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --force-delete-without-recovery >/dev/null 2>&1 || true
  aws logs delete-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
  aws ecs delete-cluster --cluster "$CLUSTER" >/dev/null 2>&1 || true
  aws iam delete-role-policy --role-name "$EXECUTION_ROLE" --policy-name read-anthropic-secret 2>/dev/null || true
  aws iam detach-role-policy --role-name "$EXECUTION_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true
  aws iam delete-role --role-name "$EXECUTION_ROLE" 2>/dev/null || true
  aws iam detach-role-policy --role-name "$INFRA_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices 2>/dev/null || true
  aws iam delete-role --role-name "$INFRA_ROLE" 2>/dev/null || true
fi

log "Listo"
