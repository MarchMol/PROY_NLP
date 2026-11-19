# Cómo continuar el proyecto

Guía para quien retome el trabajo: cómo levantar el proyecto, dónde está cada cosa y qué falta. Las razones detrás de cada decisión están en [DECISIONES.md](DECISIONES.md).

## 1. Levantar desde cero

```bash
python tasks.py setup      # .venv con Python 3.11 + dependencias fijadas (torch CPU)
cp .env.example .env       # LLM_API_KEY propia, o LLM_PROVIDER=mock (sin costo)
python tasks.py ingest     # ~10 min la 1.ª vez (baja e5 ~1.1 GB); luego segundos
python tasks.py test       # todo en verde, sin red ni costo
python tasks.py demo       # 4 preguntas demo con la línea base
```

- El snapshot de la wiki **está versionado** en `data/raw/` (D-012); no hace falta volver a descargarlo.
- `chroma/`, `data/index/`, `data/processed/` y `*.sqlite` **no** están en git: se regeneran con `ingest`.
- Los modelos se descargan solos en la caché de HuggingFace la primera vez que se usan.
- En Windows sin `make`: `python tasks.py <tarea>` (`python tasks.py --list` muestra todas).

## 2. Mapa del código (`src/marioqa/`)

| Módulo | Qué hace |
|---|---|
| `config.py` | Toda la configuración (τ, pesos, ventanas, modelos, rutas), sobreescribible por `.env` |
| `ingest/` | `download` (API MediaWiki) → `snapshot` → `clean` (wikitexto → texto, tablas linealizadas) → `chunking` → `pipeline` (comando `ingest`) |
| `index/` | `embeddings` (e5), `vector_store` (Chroma), `bm25`, `access` (regla única de rol/estado/versión), `versions` |
| `gamedata/build_db.py` | SQLite: `kingdom`, `power_moon`, `outfit`, `shop_item` |
| `guardrails/` | `input` (esquema + inyección + PII), `injection`, `pii` |
| `nlp/` | `aliases` (es↔en desde la wiki + `data/aliases_manual.json`), `ner` (gazetteer + expansión de consulta) |
| `retrieval/` | `hybrid` (vectorial + BM25 → fusión → rerank → 2.ª validación de permisos), `fusion`, `reranker`, `permissions`, `factory` |
| `llm/` | `anthropic_client` (salida estructurada), `mock` (offline), `factory` |
| `rag/` | `prompts` (contexto delimitado = datos), `baseline` (línea base) |
| `schemas.py` | Contrato de salida `{answer, sources, abstained, tool_used, needs_human_review}` |
| `eval/` | `cases` (formato del banco), `metrics`, `run_demo`, `run_retrieval` |

## 3. Estado por fase

| Fase | Estado |
|---|---|
| 0–5 | ✅ Setup, ingesta, chunking, índices + SQLite, línea base, guardrails + NER |
| 6 | ✅ Híbrida + rerank + permisos (ver D-022 sobre el reranker) |
| 7 | ⏳ Router + LangGraph + herramientas + compuerta τ + validación de salida + auditoría |
| 8 | ⏳ FastAPI + UI |
| 9 | ⏳ Banco de 33 casos + benchmark + reporte |
| 10 | ⏳ Suite de seguridad |
| 11 | ⏳ Cierre (README, informe, guion de demo) |

## 4. Pendientes, con las piezas a reutilizar

**Fase 7 (grafo con estado).** Nodos: guardrails de entrada (`check_input`) → analizador (`EntityRecognizer.analyze`) + router de intención (documental / dato_exacto / fuera_de_dominio) → herramientas → permisos → compuerta de evidencia → generador (`SYSTEM_PROMPT`, `GroundedDraft`) → validación de salida → auditoría.
- `search_documents` envuelve `HybridRetriever.search` (ya filtra por rol, versión y estado).
- `query_game_data`: SQL de solo lectura con parámetros cerrados (`entity_type`, `kingdom`, `operation`, `name`), máximo 50 filas y 2 s. El modelo **no** escribe SQL. Ojo con D-016: el infobox cuenta los Multi Moon como 3 lunas.
- Máximo 3 llamadas a herramientas por consulta. `reindex_corpus` es solo CLI de admin con confirmación y nunca se expone al LLM.
- Compuerta: abstenerse si `max_score < τ`. **τ depende del reranker** (otra escala de puntajes) y se calibra en la Fase 9.
- Citas: `draft_to_output` ya descarta ids no recuperados. Falta verificar que el fragmento contenga lo citado y que la salida no filtre el prompt.
- Degradación: si el LLM falla, devolver los fragmentos con sus fuentes.
- Auditoría: JSONL por consulta con PII ya enmascarada, solo lectura para admin.

**Fase 8.** FastAPI `/ask` y `/health`. El rol sale de la sesión, **nunca** del texto. UI con respuesta, fuentes enlazadas, banner de abstención y panel de trazabilidad.

**Fase 9.** `eval/bench.jsonl` con 33 casos en el formato de `eval/cases.py` (20 de recuperación/generación, 5 de herramientas y 8 de seguridad). `run_retrieval` ya compara configuraciones. Falta: LLM-judge (fidelidad y relevancia), métricas de abstención, de herramienta y de seguridad, y `eval/report.md`.
- Casos de versión: `python -m marioqa.ingest.download --as-of AAAA-MM-DD` + `ingest`. Hay que elegir una fecha en la que un dato realmente cambie.

**Fase 10.** 8 casos adversarios (2 por vector), incluido un chunk "envenenado" para la inyección indirecta. Los docs internos (`data/internal/`) llevan canarios `CANARIO-INT-*` para detectar fugas.

## 5. Reglas de trabajo del equipo

- Commits directo a `main`, en formato Conventional Commits con scope (`feat(ingesta): …`), y con `git add` de rutas explícitas.
- Nada de secretos en el repo: todo va por `.env`.
- Ningún dato del juego se inventa: todo sale de la wiki descargada, y lo sintético se marca como tal.
- Cada decisión técnica nueva va a `DECISIONES.md` (qué, por qué, alternativa).
