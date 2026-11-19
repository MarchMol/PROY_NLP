# Informe técnico (borrador): RAG agéntico en español sobre *Super Mario Odyssey*

> Borrador para el informe final de 10–15 páginas. Las cifras salen de `eval/report.md` (corrida del 2026-11-19, LLM y juez `claude-haiku-5-5`, corpus activo `20261119` + `20180101-asof` deprecated). Las secciones marcadas con ✍ necesitan redacción o revisión del equipo.

## 1. Problema y alcance

Los jugadores describen sus dudas de forma coloquial y en español («la luna detrás de la cascada», «el Reino de las Arenas»), mientras que la fuente autorizada, la Super Mario Wiki, está en inglés y usa nombres oficiales (*Behind the Waterfall*, *Sand Kingdom*). El sistema responde en español usando **solo** esa wiki, cita cada afirmación y se abstiene cuando no hay evidencia. ✍ Ampliar con la motivación del anteproyecto (§2–4).

## 2. Datos

- **Corpus:** 83 artículos (D-006) descargados con la API de MediaWiki, con `revid` y fecha de corte (snapshot `20261119`, versionado en git, D-008/D-012). Licencia CC BY-SA con atribución.
- **Limpieza:** árbol de wikitexto; las tablas se linealizan fila por fila y se descartan navboxes, referencias y galerías (D-009, D-013).
- **Chunking:** ventanas de 300 tokens del tokenizer de e5, con solape de 50, límite de oración y filas de tabla atómicas. Resultado: **1107 chunks** (D-010).
- **Datos exactos:** SQLite con 17 reinos, 835 Power Moons, 110 atuendos y 94 artículos de tienda (D-016).
- **Documentos internos sintéticos** (`rol_min=admin`) con canarios para probar el control de acceso.

## 3. Arquitectura

Grafo con estado en LangGraph (D-027):

1. **Guardrails de entrada** (D-019): esquema, detector de inyección por reglas y enmascarado de PII.
2. **Analizador:** NER por diccionario con 1314 alias es↔en derivados de la wiki más un glosario manual, y expansión de la consulta (D-020, D-021).
3. **Router determinista** (D-023): documental, dato exacto, mixta, fuera de dominio, ambigua o acción crítica.
4. **Herramientas** (D-026): `search_documents` (vectorial e5 + BM25 → fusión ponderada 0.6/0.4 + bono de entidad → rerank `mmarco-mMiniLMv2` sobre los 10 mejores → top-5, D-022) y `query_game_data` (SQL de solo lectura con parámetros cerrados). Lista blanca por rol, máximo 3 llamadas y timeout. `reindex_corpus` solo existe como CLI con confirmación tecleada.
5. **Segunda validación de permisos** y **compuerta de evidencia** (τ = 0.30 sobre la sigmoide del reranker).
6. **Generador** (Haiku 5.5, salida estructurada; el modelo solo cita ids, D-017) o **respuesta SQL determinista** (D-024).
7. **Guardrails de salida** (D-025): contrato, citas válidas, soporte por anclas y bloqueo de fugas.
8. **Auditoría JSONL** con todo enmascarado (D-028), más API y UI con panel de trazabilidad (D-029).

✍ Insertar las figuras 1 y 2 del anteproyecto, actualizadas con los nodos reales.

## 4. Evaluación

Banco de 33 casos (`eval/bench.jsonl`, D-030): 20 de RAG (5 directas, 3 paráfrasis, 3 identificadores, 3 compuestas, 2 ambiguas, 2 sin respuesta y 2 de versión), 5 de herramientas y 8 de seguridad. La línea base (vectorial top-5 + LLM, D-018) se corre en los grupos de RAG y seguridad.

| Métrica | Meta | Línea base | Agente |
|---|---|---|---|
| Recall@5 | ≥ 0.80 | 0.75 | **1.00** |
| MRR | ≥ 0.60 | 0.67 | **0.95** |
| Precision@5 | ≥ 0.40 | 0.49 | **0.54** |
| Fidelidad (juez) | ≥ 0.90 | 1.00 | **1.00** |
| Relevancia (juez) | ≥ 0.85 | 0.85 | **0.86** |
| Validez de citas | ≥ 0.95 | 0.96 | **0.98** |
| Abstención en casos sin respuesta | 100 % | 75 % | **100 %** |
| Abstención indebida | ≤ 10 % | 31 % | **0 %** |
| Latencia media / P95 (s) | ≤ 5 / ≤ 10 | 2.13 / 3.33 | 2.52 / 5.04 |
| Costo medio por consulta (USD) | ≤ 0.01 | 0.0004 | 0.0003 |
| Selección de herramienta | ≥ 90 % | 75 % | **100 %** |
| Ataques bloqueados | ≥ 90 % | 25 % | **100 %** |
| Accesos no autorizados denegados | 100 % | 50 % | **100 %** |

**Lectura de resultados:**
- La mejora de recuperación viene sobre todo de la búsqueda híbrida con alias es→en (D-021, D-022). La línea base falla justo en paráfrasis e identificadores en español (p. ej. «Reino de las Arenas»).
- El agente responde los datos exactos **sin LLM** (0 tokens, ~0.03 s). Por eso su costo medio es menor que el de la línea base aunque haga más trabajo.
- La línea base tiene fidelidad alta porque se abstiene mucho (25 % de abstenciones indebidas). Las respuestas que sí da son fieles, pero responde menos.
- **Todos los casos pasan** en la corrida final. El último cambio de recuperación (D-032) subió R@5 de 0.94 a 1.00 y MRR de 0.86 a 0.95, y resolvió `rag-comp-02` (definición de Multi Moon), que antes se abstenía.
- La validez de citas varió entre 0.94 y 0.99 en corridas con el mismo código: variabilidad del LLM (D-005). ✍ Reportar la media de varias corridas.

**Amenazas a la validez (✍ desarrollar):**
1. Tras la primera corrida se corrigieron reglas del router (incluida la de otros juegos, para atuendos como el de Mario 64), la consulta del reranker (D-032), un falso positivo del detector de fugas (equivalencias del tipo «cuántas vale», ambigüedad sin vocabulario y vocabulario de documentos internos) y se agregaron palabras comunes al glosario (loro→parrot, etc.). Primera corrida del agente: R@5 0.88, MRR 0.80, relevancia 0.80, ataques bloqueados 88 %, accesos denegados 50 %. Las cifras finales están medidas sobre el mismo banco que guió esos ajustes.
2. El juez es el mismo modelo que el generador (sesgo de autoevaluación). Falta revisar una muestra a mano y repetir con un juez más capaz.
3. Casos de versión: el snapshot `20180101-asof` tiene 73 artículos, porque 10 no existían en esa fecha (Balloon World, varias listas de lunas, la lista de atuendos). Un jugador nunca recibe evidencia de esa versión. Un admin que la fija obtiene abstención para Balloon World, que en enero de 2018 no existía. Solo hay 2 casos.
4. 33 casos dan intervalos de confianza amplios. Un caso cambia la tasa en ~5 puntos en RAG y en 12.5 puntos en seguridad.

## 5. Seguridad

Resumen en `docs/SEGURIDAD.md`: 8 casos adversarios (2 por vector), **8/8 bloqueados** por el agente frente a 2/8 de la línea base. La inyección indirecta se probó insertando un chunk envenenado con puntaje 0.99 en la evidencia real. La respuesta lo ignoró sin perder la respuesta correcta.

## 6. Decisiones técnicas y alternativas

Ver `docs/DECISIONES.md` (D-001 a D-031). Las más defendibles:
- reranker chico por latencia en CPU (D-022);
- router por reglas en vez de tool-calling del LLM (D-023);
- datos exactos sin LLM (D-024);
- verificación de soporte por anclas (D-025).

## 7. Limitaciones y trabajo futuro

- NER sin coincidencia difusa (errores de tipeo).
- El router por reglas no generaliza a intenciones nuevas.
- El chequeo de soporte no ve afirmaciones sin cifras ni nombres.
- Solo hay 2 snapshots y 2 casos de versión.
- τ sin calibrar formalmente.
- Juez no independiente.

✍ Mejoras: `bge-m3` como embedding, reranker cuantizado en ONNX, clasificador de intención entrenado con el banco.

## 8. Reproducibilidad

```bash
python tasks.py setup && python tasks.py ingest && python tasks.py test && python tasks.py bench
```

Dependencias fijadas (D-003), sin secretos en el repo y LLM en modo `mock` para correr sin costo.
