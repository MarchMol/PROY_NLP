# Mario Odyssey RAG: asistente en español sobre Super Mario Odyssey

Proyecto final de **CC3103 Procesamiento de Lenguaje Natural** (Universidad del Valle de Guatemala).

Es un sistema de recuperación aumentada por generación (RAG) agéntico. Responde en español preguntas de jugadores de *Super Mario Odyssey* (Power Moons, atuendos, tiendas, jefes, reinos, progresión) usando como única fuente artículos de la **Super Mario Wiki**. Cada respuesta cita las fuentes en las que se apoya.

> En construcción. Este README se completa en la fase de cierre.

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

Con `make` disponible: `make setup`, `make test`.

## Estructura

```
src/marioqa/   código del sistema (config, ingesta, índices, grafo, API)
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
