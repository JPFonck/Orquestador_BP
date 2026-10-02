#!/usr/bin/env bash
# Construye la imagen, la sube a ECR y crea (o actualiza) el servicio en ECS Express Mode.
# Se puede ejecutar cada vez que haya cambios en el código.
source "$(dirname "$0")/config.sh"
cd "$(dirname "$0")/../.."

IMAGE="$ECR_REPO_URI:$IMAGE_TAG"

log "Construyendo imagen $IMAGE"
docker build --platform linux/amd64 -t "$IMAGE" .

log "Subiendo imagen a ECR"
aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY"
docker push "$IMAGE"

SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)"
EXECUTION_ROLE_ARN="$(aws iam get-role --role-name "$EXECUTION_ROLE" --query Role.Arn --output text)"
INFRA_ROLE_ARN="$(aws iam get-role --role-name "$INFRA_ROLE" --query Role.Arn --output text)"

PRIMARY_CONTAINER="{
  \"image\": \"$IMAGE\",
  \"containerPort\": 8000,
  \"awsLogsConfiguration\": {\"logGroup\": \"$LOG_GROUP\", \"logStreamPrefix\": \"app\"},
  \"environment\": [
    {\"name\": \"LLM_PROVIDER\", \"value\": \"anthropic\"},
    {\"name\": \"STATE_BACKEND\", \"value\": \"memory\"}
  ],
  \"secrets\": [{\"name\": \"ANTHROPIC_API_KEY\", \"valueFrom\": \"$SECRET_ARN\"}]
}"
# Una sola tarea: el estado de las sesiones vive en memoria del contenedor.
SCALING='{"minTaskCount": 1, "maxTaskCount": 1}'

SERVICE_ARN="$(aws ecs describe-services --cluster "$CLUSTER" --services "$APP_NAME" \
  --query "services[?status=='ACTIVE'].serviceArn | [0]" --output text 2>/dev/null || true)"

if [ -z "$SERVICE_ARN" ] || [ "$SERVICE_ARN" = "None" ]; then
  log "Creando servicio Express $APP_NAME"
  SERVICE_ARN="$(aws ecs create-express-gateway-service \
    --cluster "$CLUSTER" \
    --service-name "$APP_NAME" \
    --execution-role-arn "$EXECUTION_ROLE_ARN" \
    --infrastructure-role-arn "$INFRA_ROLE_ARN" \
    --primary-container "$PRIMARY_CONTAINER" \
    --health-check-path /health \
    --cpu "$TASK_CPU" --memory "$TASK_MEMORY" \
    --scaling-target "$SCALING" \
    --query service.serviceArn --output text)"
else
  log "Actualizando servicio existente con la imagen $IMAGE_TAG"
  aws ecs update-express-gateway-service \
    --service-arn "$SERVICE_ARN" \
    --primary-container "$PRIMARY_CONTAINER" >/dev/null
fi

log "Esperando a que el servicio quede estable (puede tardar 5-10 minutos la primera vez)"
aws ecs wait services-stable --cluster "$CLUSTER" --services "$APP_NAME"

ENDPOINT="$(aws ecs describe-express-gateway-service --service-arn "$SERVICE_ARN" \
  --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text)"
case "$ENDPOINT" in http*) URL="$ENDPOINT" ;; *) URL="https://$ENDPOINT" ;; esac

log "Desplegado: $URL"
echo "Prueba:  ./deploy/aws/03_smoke_test.sh $URL"
echo "Docs:    $URL/docs"
