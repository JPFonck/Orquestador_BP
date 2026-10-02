#!/usr/bin/env bash
# Prepara (una sola vez) los recursos base: ECR, secreto con la API key, logs, cluster y roles IAM.
# Es idempotente: si un recurso ya existe, lo reutiliza.
#
# Uso:  ANTHROPIC_API_KEY=sk-ant-... ./deploy/aws/01_setup.sh
#       (si no está la variable, el script pide la key sin mostrarla en pantalla)
source "$(dirname "$0")/config.sh"

log "Cuenta $ACCOUNT_ID, región $AWS_REGION"

log "Repositorio ECR: $APP_NAME"
aws ecr describe-repositories --repository-names "$APP_NAME" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name "$APP_NAME" \
    --image-scanning-configuration scanOnPush=true >/dev/null

log "Secreto en Secrets Manager: $SECRET_NAME"
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" >/dev/null 2>&1; then
  echo "Ya existe (para cambiar la key: aws secretsmanager put-secret-value --secret-id $SECRET_NAME --secret-string ...)"
else
  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    read -r -s -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
    echo
  fi
  aws secretsmanager create-secret --name "$SECRET_NAME" \
    --description "API key de Anthropic para $APP_NAME" \
    --secret-string "$ANTHROPIC_API_KEY" >/dev/null
fi
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)"

log "Grupo de logs: $LOG_GROUP"
aws logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14

log "Cluster ECS: $CLUSTER"
aws ecs create-cluster --cluster-name "$CLUSTER" >/dev/null

log "Rol vinculado al servicio de ECS"
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com >/dev/null 2>&1 || true

log "Rol de ejecución de tareas: $EXECUTION_ROLE"
if ! aws iam get-role --role-name "$EXECUTION_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$EXECUTION_ROLE" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }' >/dev/null
fi
aws iam attach-role-policy --role-name "$EXECUTION_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
# Solo puede leer el secreto de esta aplicación.
aws iam put-role-policy --role-name "$EXECUTION_ROLE" --policy-name read-anthropic-secret \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"secretsmanager:GetSecretValue\", \"Resource\": \"$SECRET_ARN\"}]
  }"

log "Rol de infraestructura de Express Mode: $INFRA_ROLE"
if ! aws iam get-role --role-name "$INFRA_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$INFRA_ROLE" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }' >/dev/null
fi
aws iam attach-role-policy --role-name "$INFRA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices

log "Listo. Siguiente paso: ./deploy/aws/02_deploy.sh"
