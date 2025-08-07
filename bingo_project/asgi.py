import os
import django
from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

# Establece la variable de entorno ANTES de importar los módulos que dependen de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bingo_project.settings')
django.setup()  # Esto es crucial

# Ahora importa tus rutas y consumers
import bingo_app.routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(  # ¡Middleware de autenticación añadido!
        URLRouter(
            bingo_app.routing.websocket_urlpatterns
        )
    ),
})