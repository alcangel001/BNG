from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/messages/$', consumers.MessageConsumer.as_asgi()),
    re_path(r'ws/bingo/(?P<user_id>\d+)/$', consumers.BingoConsumer.as_asgi()),
    re_path(r'ws/user/(?P<user_id>\d+)/notifications/$', consumers.NotificationConsumer.as_asgi()),
    re_path(r'game/(?P<game_id>\d+)/$', consumers.BingoConsumer.as_asgi())
]