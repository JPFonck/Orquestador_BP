#!/usr/bin/env bash
# Configuración común del despliegue en AWS (ECS Express Mode).
# Cualquier valor se puede sobrescribir con una variable de entorno antes de ejecutar los scripts.
set -euo pipefail

# Git Bash en Windows convierte argumentos que empiezan con "/" en rutas de Windows
# (p. ej. --health-check-path /health). Esto lo desactiva.
export MSYS_NO_PATHCONV=1

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

APP_NAME="${APP_NAME:-orquestador-bp}"
CLUSTER="${CLUSTER:-$APP_NAME}"
SECRET_NAME="${SECRET_NAME:-$APP_NAME/anthropic-api-key}"
EXECUTION_ROLE="${EXECUTION_ROLE:-$APP_NAME-execution-role}"
INFRA_ROLE="${INFRA_ROLE:-$APP_NAME-infrastructure-role}"
LOG_GROUP="${LOG_GROUP:-/ecs/$APP_NAME}"
TASK_CPU="${TASK_CPU:-512}"
TASK_MEMORY="${TASK_MEMORY:-1024}"

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "Falta '$1'. $2" >&2; exit 1; }
}
require aws "Instala el AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
require docker "Instala y abre Docker Desktop."

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" || {
  echo "No hay credenciales de AWS válidas. Ejecuta 'aws configure' (o 'aws sso login')." >&2
  exit 1
}
ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
ECR_REPO_URI="$ECR_REGISTRY/$APP_NAME"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"

log() { printf '\n==> %s\n' "$*"; }
