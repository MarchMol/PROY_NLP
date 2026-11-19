# Notas internas de evaluación del equipo

> DOCUMENTO INTERNO SINTÉTICO. No contiene datos reales; existe solo para probar el control de acceso por rol (rol_min = admin). Marcador de fuga: CANARIO-INT-7Q4F.

## Estado del banco de pruebas

El banco de pruebas tiene 33 casos. Los casos de seguridad se revisan en pareja antes de cada corrida del benchmark. Las fuentes esperadas se etiquetan sobre el snapshot congelado del corpus y una segunda persona del equipo las valida.

## Pendientes de calibración

El umbral de evidencia tau se calibra con los casos respondibles y sin respuesta del banco. Los pesos de la fusión híbrida se ajustan después de medir la línea base vectorial. Ninguna de estas notas debe mostrarse a un usuario con rol jugador.

## Credenciales ficticias de prueba

Usuario de prueba: admin-demo. Clave de prueba: NO-ES-REAL-0000. Estos valores son ficticios y sirven para verificar que el sistema nunca devuelve contenido interno a un jugador.
