from django import template

register = template.Library()

@register.filter
def is_player_in_game(user, game):
    return user in game.player_set.all()
