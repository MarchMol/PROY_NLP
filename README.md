# Mario Odyssey RAG: asistente en español sobre Super Mario Odyssey

Proyecto final de **CC3103 Procesamiento de Lenguaje Natural** (Universidad del Valle de Guatemala).

Es un sistema de recuperación aumentada por generación (RAG) agéntico. Responde en español preguntas de jugadores de *Super Mario Odyssey* (Power Moons, atuendos, tiendas, jefes, reinos, progresión) usando como única fuente artículos de la **Super Mario Wiki**. Cada respuesta cita las fuentes en las que se apoya.

## Cómo funciona

```
pregunta ─▶ guardrails de entrada ─▶ NER + alias es→en ─▶ router ─┬─▶ query_game_data (SQLite, solo lectura)
            (esquema, inyección, PII)                             ├─▶ search_documents (vectorial + BM25 → fusión → rerank)
                                                                  └─▶ rechazo controlado (fuera de dominio, ambigua, acción crítica)
      ─▶ 2.ª validación de permisos ─▶ compuerta de evidencia τ ─▶ generador con citas ─▶ guardrails de salida ─▶ auditoría JSONL
```

- **Salida:** JSON validado `{answer, sources, abstained, tool_used, needs_human_review}`. Cada fuente apunta a un fragmento realmente recuperado (artículo, sección, `chunk_id` y URL).
- **Roles:** `jugador` (por defecto) y `admin` (sesión firmada con `ADMIN_TOKEN`). El rol nunca se toma del texto de la pregunta.
- **Decisiones técnicas:** [docs/DECISIONES.md](docs/DECISIONES.md). **Estado y pendientes:** [docs/CONTINUAR.md](docs/CONTINUAR.md). **Resultados:** [eval/report.md](eval/report.md). **Seguridad:** [docs/SEGURIDAD.md](docs/SEGURIDAD.md). **Demo:** [docs/GUION_DEMO.md](docs/GUION_DEMO.md).

## Requisitos

- Python **3.11** (en Windows: `py -3.11`)
- Sin GPU. Los modelos de embeddings y reranking corren en CPU.
- Opcional: `make`. En Windows, todas las tareas también se pueden correr con `python tasks.py <tarea>`.

## Instalación rápida

```bash
python tasks.py setup        # crea .venv e instala dependencias fijadas
cp .env.example .env         # luego edita .env (LLM_PROVIDER, LLM_API_KEY, WIKI_CONTACT)
python tasks.py test         # corre los tests (sin red, LLM en modo mock)
python tasks.py ingest       # un comando: chunks + Chroma + BM25 + SQLite (idempotente)
```

`ingest` usa los snapshots versionados en `data/raw/`; si no hay ninguno, descarga uno. La primera vez baja el modelo de embeddings (~1.1 GB) y tarda varios minutos en CPU; las siguientes solo re-embeben lo que cambió.

Otras tareas: `ingest-download` (snapshot nuevo de la wiki) e `ingest-chunks` (solo chunks, ventanas 200/300/500).

Con `make` disponible: `make setup`, `make test`, `make run`, `make bench`.

## Uso

```bash
python tasks.py run          # API + UI en http://127.0.0.1:8000
python tasks.py bench        # benchmark de 33 casos (línea base vs agente) → eval/report.md
python tasks.py security     # solo la suite adversaria (8 casos) → eval/security_report.md
python -m marioqa.agent.factory "¿Cuántas lunas hay en el Reino de las Arenas?"   # una pregunta por CLI
```

API:

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/ask` | `{"question": "...", "version": null}` → `{output, trace, latency_s, cost_usd, tokens}`. `version` solo se respeta para admin |
| `GET` | `/health` | Estado del agente, índice, SQLite y LLM |
| `POST` | `/login` · `/logout` | `{"token": ADMIN_TOKEN}` → cookie de sesión `admin` firmada |
| `GET` | `/audit?limit=20` | Últimos registros de auditoría (solo admin) |

Acción administrativa (nunca expuesta al agente ni a la API):

```bash
python -m marioqa.tools.reindex --version 20261119 --confirm   # pide teclear la versión
```

## Estructura

```
src/marioqa/   código: ingest/ index/ gamedata/ nlp/ retrieval/ guardrails/ llm/ rag/
               agent/ (grafo, router, salida, auditoría) tools/ api/ (FastAPI + UI) eval/
tests/         tests unitarios e integración (sin red por defecto)
data/          lista de artículos semilla; snapshots e índices se generan aquí (no se versionan)
eval/          banco de 33 casos, runner del benchmark y reportes
docs/          anteproyecto, decisiones técnicas, seguridad
scripts/       utilidades de línea de comandos
```

## Licencia y atribución

- **Contenido:** el texto del corpus proviene de la [Super Mario Wiki](https://www.mariowiki.com) y se publica bajo licencia **Creative Commons Atribución-CompartirIgual**: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) para aportes desde el 27 de febrero de 2026 y [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) para los anteriores. Ver [MarioWiki:Copyrights](https://www.mariowiki.com/MarioWiki:Copyrights).
  - Solo se usa **texto**, obtenido con la **API oficial de MediaWiki** (no scraping), con un User-Agent propio y respetando sus límites.
  - Cada respuesta del sistema cita el artículo y la sección de origen, con enlace al artículo.
  - Cualquier obra derivada del corpus (snapshots, chunks, tablas SQLite) queda bajo la misma licencia CC BY-SA.
  - La wiki no garantiza la exactitud de su contenido. Por eso el sistema cita siempre y se abstiene cuando no tiene evidencia suficiente.
- **Sin afiliación:** este es un proyecto académico sin ninguna afiliación con Nintendo ni con la Super Mario Wiki. *Super Mario Odyssey* y sus personajes son marcas de Nintendo.
- **Documentos internos:** los documentos con `rol_min = admin` son **sintéticos** y no contienen datos reales. Solo existen para probar el control de acceso.

## Equipo

Francis Aguilar · Gabriel Gerardo Pineda · Paula Rebeca Barillas · José Marchena
