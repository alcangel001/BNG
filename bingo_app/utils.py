import random
 

def generate_bingo_card():
    """Genera un cartón de Bingo 5x5 con números únicos"""
    card = []
    for _ in range(5):
        row = sorted(random.sample(range(1, 91), 5))
        card.append(row)
    return card

def get_pattern_description(pattern):
    descriptions = {
        'HORIZONTAL': 'Gana completando una línea horizontal',
        'VERTICAL': 'Gana completando una línea vertical',
        'DIAGONAL': 'Gana completando las dos diagonales (X)',
        'FULL': 'Gana completando todo el cartón',
        'CORNERS': 'Gana marcando las cuatro esquinas'
    }
    return descriptions.get(pattern, '')

    