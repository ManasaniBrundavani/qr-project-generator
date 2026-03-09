import qrcode
import os
from PIL import Image, ImageDraw, ImageFont


def generate_qr(data: str, filename: str, label: str = "") -> str:
    """Generate a QR code image from *data* and save it under *filename*.

    The file is written to a ``qr_codes`` directory at the project root (created
    if it doesn't exist).  The function returns the path to the saved PNG file.
    """
    if not os.path.exists("qr_codes"):
        os.makedirs("qr_codes")

    path = f"qr_codes/{filename}.png"

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=5
    )
    qr.add_data(data)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if label:
        font = ImageFont.load_default()
        text = label.strip()
        draw = ImageDraw.Draw(qr_img)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width = right - left
        text_height = bottom - top

        padding = 14
        extra_height = text_height + (padding * 2)
        canvas_width = max(qr_img.width, text_width + (padding * 2))
        canvas = Image.new("RGB", (canvas_width, qr_img.height + extra_height), "white")

        qr_x = (canvas_width - qr_img.width) // 2
        canvas.paste(qr_img, (qr_x, 0))

        draw = ImageDraw.Draw(canvas)
        text_x = (canvas_width - text_width) // 2
        text_y = qr_img.height + padding
        draw.text((text_x, text_y), text, fill="black", font=font)
        canvas.save(path)
    else:
        qr_img.save(path)

    return path
