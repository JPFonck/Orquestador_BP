# Despliegue en AWS (ECS Express Mode)

Guía para publicar el orquestador en AWS y probarlo con Claude real. El servicio corre en **Amazon ECS Express Mode**: AWS crea el balanceador, los grupos de seguridad y una URL HTTPS pública a partir de la imagen del contenedor. App Runner, que era la alternativa más simple, no acepta clientes nuevos desde el 30 de abril de 2026.

```
Tu PC ──docker push──► ECR ──► ECS Express Mode (Fargate, 1 tarea) ──► API de Anthropic
                                   ▲              │
                     URL HTTPS ────┘              └── API key leída desde Secrets Manager
```

## 1. Crear la cuenta y el acceso (una sola vez)

1. **Cuenta de AWS**: regístrate en https://aws.amazon.com (piden tarjeta de crédito). Al entrar con el usuario raíz, activa **MFA** en *Security credentials*.
2. **Alerta de costos** (recomendado): en *Billing → Budgets*, crea un presupuesto mensual (p. ej. US$20) con aviso por correo.
3. **Usuario para el CLI**: en *IAM → Users → Create user* crea `deployer`. En permisos elige *Attach policies directly* → `AdministratorAccess` (válido para una cuenta de pruebas; en una cuenta compartida pide permisos acotados). Luego, en el usuario, *Security credentials → Create access key → Command Line Interface* y guarda el *Access key ID* y el *Secret access key*.
4. **AWS CLI v2**: instálalo desde https://awscli.amazonaws.com/AWSCLIV2.msi y abre una terminal nueva.
5. **Configurar credenciales**:
   ```bash
   aws configure
   # AWS Access Key ID:     <tu access key>
   # AWS Secret Access Key: <tu secret key>
   # Default region name:   us-east-1
   # Default output format: json
   aws sts get-caller-identity   # debe mostrar tu número de cuenta
   ```

## 2. Preparar los recursos base (una sola vez)

Con Docker Desktop abierto, desde la raíz del repo en **Git Bash**:

```bash
./deploy/aws/01_setup.sh
```

Pide la API key de Anthropic sin mostrarla (o la toma de `ANTHROPIC_API_KEY` si está definida) y crea:

| Recurso | Nombre | Para qué |
|---|---|---|
| Repositorio ECR | `orquestador-bp` | Imágenes del contenedor |
| Secreto | `orquestador-bp/anthropic-api-key` | API key; nunca va dentro de la imagen |
| Log group | `/ecs/orquestador-bp` | Logs del contenedor (14 días) |
| Cluster ECS | `orquestador-bp` | Donde corre el servicio |
| Rol `orquestador-bp-execution-role` | | Descargar la imagen, escribir logs y leer **solo** ese secreto |
| Rol `orquestador-bp-infrastructure-role` | | Permite a ECS crear el balanceador y la red del servicio |

## 3. Desplegar

```bash
./deploy/aws/02_deploy.sh
```

Construye la imagen (`linux/amd64`), la sube a ECR y crea el servicio, o lo actualiza si ya existe. La primera vez tarda entre 5 y 10 minutos. Al final imprime la URL pública. Para publicar cambios del código, haz commit y vuelve a ejecutar el mismo script (la imagen se etiqueta con el hash del commit).

## 4. Probar

```bash
./deploy/aws/03_smoke_test.sh https://<endpoint>
```

Ejecuta los casos aprobado, riesgo alto e identidad ambigua con prueba de vida, y muestra la auditoría de tools. Cada caso tarda unos 30 s porque llama a Claude. También puedes probar desde el navegador en `https://<endpoint>/docs`.

Logs en vivo:
```bash
aws logs tail /ecs/orquestador-bp --follow
```

## 5. Apagar para no pagar

```bash
./deploy/aws/99_teardown.sh          # borra el servicio y su balanceador (lo que cuesta por hora)
./deploy/aws/99_teardown.sh --all    # además borra ECR, secreto, logs, cluster y roles
```

## Costos aproximados (us-east-1)

| Concepto | Costo |
|---|---|
| Fargate, 1 tarea de 0.5 vCPU / 1 GB | ~US$0.6 por día |
| Application Load Balancer | ~US$0.6 por día |
| Secrets Manager, ECR, logs, IP pública | céntimos |
| API de Claude | céntimos por onboarding (5-7 llamadas por solicitud) |

En total, alrededor de **US$1-1.5 por día** mientras el servicio está arriba. `99_teardown.sh` lo detiene.

## Limitaciones de este despliegue de pruebas

- **Una sola tarea con estado en memoria.** Las sesiones se pierden si el contenedor se reinicia o se vuelve a desplegar. Para varias tareas o persistencia real, el siguiente paso es un `StateRepository` sobre DynamoDB: la interfaz ya está en [app/state/repository.py](../app/state/repository.py).
- **Tiempo de respuesta.** `/start` es síncrono y tarda ~30 s con Claude, por debajo de los 60 s que el balanceador espera por defecto. Si ves errores 504, sube el *idle timeout* del balanceador en *EC2 → Load Balancers → Attributes*.
- **API pública sin autenticación.** Sirve para pruebas. Antes de exponer datos reales, protégela (Cognito/JWT, WAF o acceso privado) y apágala cuando no la uses.
