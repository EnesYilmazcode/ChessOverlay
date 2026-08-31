"""Generate holochess.ico - the cyan hologram arrow over a dark board corner."""
from PIL import Image, ImageDraw, ImageFilter

SIZES = [256, 128, 64, 48, 32, 16]
BG      = (38, 36, 33, 255)
DARK_SQ = (118, 150, 86, 255)
LIGHT_SQ= (238, 238, 210, 255)
CYAN    = (0, 232, 255, 255)
GLOW    = (0, 232, 255, 70)


def render(size):
    scale = 8
    side = size * scale
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = int(side * 0.20)
    draw.rounded_rectangle([0, 0, side - 1, side - 1], radius=radius, fill=BG)

    # a 4x4 checker inset so it reads as a board even when tiny
    pad = int(side * 0.14)
    board = side - pad * 2
    cell = board / 4.0
    for row in range(4):
        for col in range(4):
            colour = LIGHT_SQ if (row + col) % 2 == 0 else DARK_SQ
            x0 = pad + col * cell
            y0 = pad + row * cell
            draw.rectangle([x0, y0, x0 + cell, y0 + cell], fill=colour)

    # the hologram arrow, pointing up and to the right
    cx, cy = side / 2.0, side / 2.0
    length = side * 0.46
    tail = (cx - length * 0.42, cy + length * 0.46)
    tip  = (cx + length * 0.44, cy - length * 0.44)

    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    dist = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / dist, dy / dist
    nx, ny = -uy, ux

    shaft = side * 0.085
    head_w = side * 0.24
    head_l = side * 0.26
    base = (tip[0] - ux * head_l, tip[1] - uy * head_l)

    def off(point, amount):
        return (point[0] + nx * amount, point[1] + ny * amount)

    arrow = [
        off(tail,  shaft / 2), off(base,  shaft / 2), off(base,  head_w / 2),
        tip,
        off(base, -head_w / 2), off(base, -shaft / 2), off(tail, -shaft / 2),
    ]

    # soft glow: a few scaled-up copies of the arrow at low alpha, blurred
    glow = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(arrow, fill=GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(side * 0.035))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    draw.polygon(arrow, fill=CYAN)
    draw.line(arrow + [arrow[0]], fill=(215, 255, 255, 255),
              width=max(1, int(side * 0.010)), joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main():
    frames = [render(s) for s in SIZES]
    frames[0].save("holochess.ico", format="ICO",
                   sizes=[(s, s) for s in SIZES])
    frames[0].resize((256, 256), Image.LANCZOS).save("icon_preview.png")
    print("wrote holochess.ico with sizes", SIZES)


if __name__ == "__main__":
    main()
