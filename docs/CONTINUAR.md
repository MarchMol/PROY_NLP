# Cómo continuar el proyecto

Guía para quien retome el trabajo: cómo levantar el proyecto, dónde está cada cosa y qué falta. Las razones detrás de cada decisión están en [DECISIONES.md](DECISIONES.md).

## 1. Levantar desde cero

```bash
python tasks.py setup      # .venv con Python 3.11 + dependencias fijadas (torch CPU)
cp .env.example .env       # LLM_API_KEY propia, o LLM_PROVIDER=mock (sin costo)
python tasks.py ingest     # ~10 min la 1.ª vez (baja e5 ~1.1 GB); luego segundos
python tasks.py test       # todo en verde, sin red ni costo
python tasks.py demo       # 4 preguntas demo con la línea base
python tasks.py run        # API + UI en http://127.0.0.1:8000
python tasks.py bench      # 33 casos, línea base vs agente → eval/report.md
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
| 7 | ✅ Router + LangGraph + herramientas + compuerta τ + validación de salida + auditoría (D-023…D-028) |
| 8 | ✅ FastAPI (`/ask`, `/health`, `/login`, `/audit`) + UI con trazabilidad (D-029) |
| 9 | ✅ Banco de 33 casos + benchmark + `eval/report.md` (D-030); snapshots `20261119` (activa) y `20180101-asof` |
| 10 | ✅ Suite de seguridad (`python tasks.py security`, `docs/SEGURIDAD.md`) |
| 11 | ✅ README, `docs/INFORME.md` (borrador), `docs/GUION_DEMO.md` |

Módulos nuevos: `agent/` (`graph`, `router`, `sql_answers`, `output_guard`, `audit`, `factory`), `tools/` (`search`, `game_data`, `reindex`, `base`), `api/` (`app`, `session`, `static/index.html`), `eval/` (`judge`, `run_bench`).

## 4. Pendientes

1. **Redes con proxy:** si `download` falla con `CERTIFICATE_VERIFY_FAILED`, la red está interceptando TLS. Usa otra red (p. ej. un hotspot) en lugar de desactivar la verificación.
2. **Revisión manual** de una muestra de las puntuaciones del juez (fidelidad y relevancia) y repetir el juez con un modelo más capaz (`LLM_JUDGE_MODEL=claude-sonnet-5-5`) para reducir el sesgo de autoevaluación.
3. **Calibrar τ** con el banco: hoy 0.30 sobre la sigmoide del reranker. Desde D-032 los puntajes son más bajos (~0.4–0.5 en algunos casos válidos), así que no subas τ sin medir. Los puntajes máximos de cada caso están en `eval/results/bench_agente.json` (traza) y en la auditoría.
4. **Etiquetado:** una segunda persona del equipo debe revisar las fuentes esperadas de `eval/bench.jsonl` (anteproyecto §12).
5. **Sesgo de evaluación:** las correcciones del router (consultas de equivalencia y ambigüedad sin vocabulario) y las palabras nuevas del glosario se agregaron tras la primera corrida del banco. Conviene un pequeño conjunto de validación aparte para el informe.
6. Informe final de 10–15 páginas a partir de `docs/INFORME.md`.

## 5. Reglas de trabajo del equipo

- Commits directo a `main`, en formato Conventional Commits con scope (`feat(ingesta): …`), y con `git add` de rutas explícitas.
- Nada de secretos en el repo: todo va por `.env`.
- Ningún dato del juego se inventa: todo sale de la wiki descargada, y lo sintético se marca como tal.
- Cada decisión técnica nueva va a `DECISIONES.md` (qué, por qué, alternativa).
