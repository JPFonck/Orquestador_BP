# Orquestador de Onboarding Digital

Servicio FastAPI con un **agente orquestador** que coordina agentes especializados (Claude) durante el onboarding digital de un cliente bancario: verifica identidad, consulta listas de riesgo, prepara la documentación según la política del banco, decide si el prospecto es apto y redacta la respuesta al cliente. Cuando una verificación falla o es ambigua, propone una mitigación concreta.

## Arquitectura

```
POST /start ──► OnboardingOrchestrator (máquina de estados)
                 │
                 ├─ IDENTITY_AND_RISK ─┬─ IdentityAgent ──► verify_identity, verify_biometrics
                 │   (en paralelo)     └─ RiskAgent ──────► check_risk_lists
                 ├─ GUARDRAILS ────────── reglas duras de la política (código, no LLM)
                 ├─ DOCUMENTATION ─────── DocumentationAgent ► get_product_policy, prepare_documentation
                 ├─ DECISION ──────────── DecisionAgent (sin tools) ► validado contra los guardrails
                 └─ RESPONSE ──────────── ResponseAgent (sin tools) ► filtro de información interna
                 ▼
     APPROVED | REJECTED | PENDING_REVIEW | NEEDS_INFO   (estado persistido tras cada paso)
```

| Componente | Archivo |
|---|---|
| Orquestador principal | [app/orchestrator/orchestrator.py](app/orchestrator/orchestrator.py) |
| Reglas duras / mitigaciones | [app/orchestrator/guardrails.py](app/orchestrator/guardrails.py), [app/orchestrator/mitigation.py](app/orchestrator/mitigation.py) |
| Sub-agentes | [app/agents/](app/agents/) (loop agéntico en [base.py](app/agents/base.py)) |
| Allowlist de tools + auditoría | [app/tools/registry.py](app/tools/registry.py) |
| Tools mockeadas | [app/tools/mocks.py](app/tools/mocks.py), datos en [app/data/](app/data/) |
| Políticas del banco | [app/policies/policies.yaml](app/policies/policies.yaml) |
| Estado y persistencia | [app/state/models.py](app/state/models.py), [app/state/repository.py](app/state/repository.py) |
| API | [app/api/routes.py](app/api/routes.py) |

### Decisiones de diseño

- **Control de tools en dos capas.** Cada agente solo recibe en `tools=` las de su allowlist, y además `ToolRegistry.execute` vuelve a validar cada `tool_use`: una tool no autorizada se responde con `tool_result` `is_error: true` y se registra un evento `policy_violation`. Toda invocación queda en `state.tool_calls`.
- **Las tools son la fuente de verdad.** Los guardrails usan lo que devolvieron las tools, no lo que el LLM dice que devolvieron; si difieren, se registra en `discrepancies`.
- **El LLM decide dentro de límites.** Las reglas duras (confianza < 0.8 → escalamiento, riesgo `high` → cumplimiento, identidad no verificada → rechazo) se aplican en código. El agente de decisión puede ser más conservador, nunca menos; si contradice una regla se sobrescribe (`guardrail_override`).
- **Mitigaciones de un catálogo cerrado**: cada propuesta corresponde a un procedimiento real del banco (re-verificación biométrica, videollamada, validación presencial, debida diligencia reforzada, escalamiento a cumplimiento, revisión manual).
- **Fallos controlados.** Errores de tools o del LLM se reintentan con backoff exponencial; si persisten, la solicitud pasa a revisión manual. `stop_reason: refusal` se trata como fallo de agente y se habilita el fallback de modelo del lado del servidor.
- **Confidencialidad.** El mensaje al cliente pasa por un filtro de salida: si menciona listas de sanciones, PEP o niveles de riesgo, se reemplaza por una plantilla segura. Las notas del revisor nunca llegan al agente de respuesta.

### Modelo

`claude-opus-5-5` con thinking adaptativo, salida estructurada (`output_config.format`) y tools con `strict: true`. Effort `low` en los sub-agentes y `medium` en la decisión del orquestador (configurable).

## Ejecución

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env            # completar ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

Sin API key se puede usar `LLM_PROVIDER=mock`: un LLM determinista que imita el comportamiento de cada agente para ver el flujo completo. Para persistir sesiones entre reinicios: `STATE_BACKEND=sqlite`.

Documentación interactiva: http://localhost:8000/docs

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/onboarding/start` | Inicia el onboarding y ejecuta el flujo completo |
| GET | `/api/v1/onboarding/{session_id}` | Estado completo: resultados, decisión, auditoría de tools, eventos y conversación |
| POST | `/api/v1/onboarding/{session_id}/messages` | Turno del cliente: aporta evidencia (`liveness_token`), corrige el producto o pregunta |
| POST | `/api/v1/onboarding/{session_id}/review` | Resolución humana de una solicitud `PENDING_REVIEW` |
| GET | `/health` | Estado del servicio |

```bash
curl -X POST localhost:8000/api/v1/onboarding/start -H "Content-Type: application/json" \
  -d '{"prospect_name": "Juan Perez", "document_id": "1712345678", "product": "cuenta_ahorros"}'
```

```json
{
  "session_id": "…",
  "status": "APPROVED",
  "current_step": "COMPLETED",
  "decision": {"decision": "APPROVED", "rationale": "…", "overridden_by_guardrails": false, "decided_by": "orchestrator"},
  "required_documents": [{"code": "CEDULA", "name": "Cédula de identidad vigente", "description": "…"}, "…"],
  "mitigations": [],
  "customer_message": "Hola Juan Perez, …"
}
```

Flujo conversacional (identidad ambigua → prueba de vida → aprobación):

```bash
curl -X POST localhost:8000/api/v1/onboarding/start -H "Content-Type: application/json" \
  -d '{"prospect_name": "Maria Lopez", "document_id": "0923456789", "product": "cuenta_ahorros"}'
# → PENDING_REVIEW con mitigación BIOMETRIC_REVERIFICATION

curl -X POST localhost:8000/api/v1/onboarding/<session_id>/messages -H "Content-Type: application/json" \
  -d '{"message": "Ya hice la prueba de vida", "liveness_token": "LIVENESS-OK"}'
# → APPROVED (solo se repite la verificación de identidad; el resultado de riesgo se reutiliza)
```

## Escenarios de prueba (datos mock)

| Prospecto | Documento | Producto | Resultado | Mitigación |
|---|---|---|---|---|
| Juan Perez | 1712345678 | cuenta_ahorros | APPROVED | — |
| Maria Lopez | 0923456789 | cuenta_ahorros | PENDING_REVIEW (confianza 0.72) | Re-verificación biométrica |
| Carlos Ruiz | 1104567890 | cuenta_ahorros | PENDING_REVIEW (confianza 0.40) | Videollamada |
| cualquiera | 1708765432 | cuenta_ahorros | REJECTED (no verificado) | Validación presencial |
| Pedro Gomez | 1705555555 | tarjeta_credito | PENDING_REVIEW (riesgo high) | Escalamiento a cumplimiento |
| Ana Torres | 0102030405 | cuenta_corriente | APPROVED (riesgo medium) | Debida diligencia reforzada |
| cualquiera | 9999999999 | cuenta_ahorros | PENDING_REVIEW (timeout tras reintentos) | Revisión manual |
| cualquiera | 1712345678 | hipoteca | NEEDS_INFO | Elegir producto disponible |

Tokens de prueba de vida: `LIVENESS-OK` (coincide, 0.97) y `LIVENESS-FAIL` (no coincide → videollamada).

Nota: el mock valida el formato de la cédula (10 dígitos, provincia 01-24/30, tercer dígito < 6) pero no el dígito verificador, porque el documento del enunciado (`1712345678`) no lo cumple.

## Pruebas

```bash
pytest            # suite completa con el LLM mock (no requiere API key)
pytest -m live    # integración contra Claude real (requiere ANTHROPIC_API_KEY)
```
