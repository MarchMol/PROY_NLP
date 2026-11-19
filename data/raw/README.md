# Snapshots crudos de la Super Mario Wiki

Cada carpeta `<AAAAMMDD>[-asof]/` es una foto del corpus. `manifest.json` registra la fecha de corte y, por cada artículo, su `revid`, timestamp, URL y SHA-256. En `pages/` hay un JSON por artículo con el wikitexto crudo y el expandido.

Se versionan en git para que el corpus, los índices y el banco de pruebas sean reproducibles aunque la wiki cambie.

**Licencia:** el contenido proviene de la [Super Mario Wiki](https://www.mariowiki.com) y se distribuye bajo [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (aportes desde el 27-02-2026) o [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) (aportes anteriores). La autoría corresponde a sus colaboradores; el historial de cada artículo está en la URL del manifest. Ver [MarioWiki:Copyrights](https://www.mariowiki.com/MarioWiki:Copyrights).
