"""Load plans (PDF, TIFF, PNG, JPEG) as images with their resolution. No bpy.

PDF pages are rendered with PDFium (pypdfium2). A scanned PDF holds one
large image; the page is rendered at that image's resolution, so no detail
is lost or invented. Other formats are read with Pillow, which also reads
the 1-bit CCITT compressed TIFFs of scanned plans.

The result is written as PNG for Blender, with the plan's own colors. With
"white transparent", its alpha marks the white paper for Solid view.
"""

import io
import math
import os

PDF_EXTENSIONS = {".pdf"}
RASTER_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
EXTENSIONS = PDF_EXTENSIONS | RASTER_EXTENSIONS

MAX_PIXELS = 400_000_000  # beyond this, the PDF is rendered at a lower resolution
PAPER_LEVEL = 230  # pixels this light in every channel count as paper


class Sheet:
    """A loaded plan: Pillow image and its resolution in pixels per inch."""

    def __init__(self, image, dpi_x, dpi_y, note=""):
        self.image = image
        self.dpi_x = dpi_x
        self.dpi_y = dpi_y
        self.note = note

    @property
    def width_m(self):
        return self.image.width / self.dpi_x * 0.0254

    @property
    def height_m(self):
        return self.image.height / self.dpi_y * 0.0254


def pdf_page_count(path):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _scan_resolution(page):
    """Resolution of the largest image on the page, if it covers most of it.

    Returns (dpi, gray), or (None, False) for pages without such an image
    (vector drawings). The image matrix maps the image's unit square onto
    the page, so its column lengths are the drawn size in points.
    """
    import pypdfium2.raw as raw
    page_w, page_h = page.get_size()
    best = None
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        a, b, c, d, _, _ = obj.get_matrix().get()
        w_pt, h_pt = math.hypot(a, b), math.hypot(c, d)
        if w_pt * h_pt < 0.25 * page_w * page_h:
            continue
        px_w, px_h = obj.get_px_size()
        dpi = max(px_w / (w_pt / 72), px_h / (h_pt / 72))
        meta = obj.get_metadata()
        gray = meta.bits_per_pixel <= 8 and meta.colorspace in (1, 2)  # DeviceGray, CalGray
        if best is None or dpi > best[0]:
            best = (dpi, gray)
    return best or (None, False)


def load_pdf(path, page_index=0, dpi=0.0):
    """Render one PDF page. dpi 0 means the scan's own resolution, else 300."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        if not 0 <= page_index < len(pdf):
            raise ValueError(f"The PDF has {len(pdf)} pages, there is no page {page_index + 1}")
        page = pdf[page_index]
        page_w, page_h = page.get_size()
        scan_dpi, gray = _scan_resolution(page)
        note = ""
        if dpi <= 0:
            dpi = round(scan_dpi) if scan_dpi else 300.0
            note = f"scanned at {dpi:.0f} dpi" if scan_dpi else "vector page, rendered at 300 dpi"
        pixels = page_w / 72 * dpi * page_h / 72 * dpi
        if pixels > MAX_PIXELS:
            dpi *= math.sqrt(MAX_PIXELS / pixels)
            note = f"rendered at {dpi:.0f} dpi to stay below {MAX_PIXELS / 1e6:.0f} megapixels"
        bitmap = page.render(scale=dpi / 72, grayscale=gray)
        image = bitmap.to_pil()
        # The real resolution after rounding the pixel size.
        return Sheet(image, image.width / (page_w / 72), image.height / (page_h / 72), note)
    finally:
        pdf.close()


def load_raster(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # plans are large on purpose
    image = Image.open(path)
    image.seek(0)
    image.load()
    dpi = image.info.get("dpi")
    note = ""
    if not dpi or not dpi[0] or not dpi[1]:
        dpi = (300.0, 300.0)
        note = "no resolution in the file, 300 dpi assumed: check the scale"
    return Sheet(image, float(dpi[0]), float(dpi[1]), note)


def load(path, page_index=0, dpi=0.0):
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTENSIONS:
        return load_pdf(path, page_index, dpi)
    if ext in RASTER_EXTENSIONS:
        return load_raster(path)
    raise ValueError(f"Cannot read {ext} files")


def _is_black_and_white(gray):
    """True if almost all pixels are black or white (a scan of a line drawing)."""
    hist = gray.histogram()
    total = gray.width * gray.height
    return (hist[0] + hist[255]) >= 0.995 * total


def write_png(sheet, path, white_transparent=True):
    """Write the sheet as PNG. Returns a short description of the format."""
    import numpy as np
    from PIL import Image
    image = sheet.image
    if image.mode in ("1", "L", "LA", "I", "I;16", "F"):
        image = image.convert("L")
    else:
        image = image.convert("RGB")
        r, g, b = (np.asarray(c) for c in image.split())
        if np.array_equal(r, g) and np.array_equal(g, b):
            image = Image.fromarray(r)
    # The colors stay as they are; the shader uses them unchanged. With
    # white transparent, the alpha only marks the paper (near-white pixels)
    # for Solid view, which cuts the image off at its alpha.
    if image.mode == "L" and _is_black_and_white(image):
        bw = image.point(lambda v: 255 if v >= 128 else 0).convert("1")
        if white_transparent:
            # 1-bit palette, white transparent.
            index = image.point(lambda v: 1 if v >= 128 else 0)
            pal = Image.frombytes("P", image.size, index.tobytes())
            pal.putpalette([0, 0, 0, 255, 255, 255])
            pal.save(path, transparency=bytes([255, 0]), bits=1, optimize=True)
            return "black and white, white transparent"
        bw.save(path, optimize=True)
        return "black and white"
    if not white_transparent:
        image.save(path)
        return "grayscale" if image.mode == "L" else "color"
    pixels = np.asarray(image)
    lowest = pixels if pixels.ndim == 2 else pixels.min(axis=2)
    alpha = np.where(lowest >= PAPER_LEVEL, 0, 255).astype(np.uint8)
    if image.mode == "L":
        Image.merge("LA", (image, Image.fromarray(alpha))).save(path)
        return "grayscale, white transparent"
    image.putalpha(Image.fromarray(alpha))
    image.save(path)
    return "color, white transparent"


def gray_pixels(data_or_path):
    """Grayscale pixels (numpy uint8, row 0 at the top) of a PNG file or bytes."""
    import numpy as np
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    source = io.BytesIO(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) else data_or_path
    with Image.open(source) as image:
        if image.mode in ("LA", "RGBA", "PA") or "transparency" in image.info:
            # Transparent white: put the image on white before graying.
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            return np.asarray(white.convert("L"))
        return np.asarray(image.convert("L"))


def find_mark_center(gray, px, py, radius):
    """Center of the small dark mark (a grid cross) nearest to (px, py).

    gray: numpy uint8 image, row 0 at the top; px, py in pixels with y
    downwards. Looks at the dark pixels in a square of +-radius pixels.
    Marks that run out of the square are lines, not crosses, and are
    skipped. Returns (x, y) of the center of gravity, or None.
    """
    h, w = gray.shape
    x0, x1 = max(0, int(px - radius)), min(w, int(px + radius) + 1)
    y0, y1 = max(0, int(py - radius)), min(h, int(py + radius) + 1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    window = gray[y0:y1, x0:x1] < 128
    rows, cols = window.shape
    seen = [[False] * cols for _ in range(rows)]
    dark = window.tolist()
    best = None
    for r in range(rows):
        for c in range(cols):
            if not dark[r][c] or seen[r][c]:
                continue
            stack = [(r, c)]
            seen[r][c] = True
            pixels = []
            touches_edge = False
            while stack:
                rr, cc = stack.pop()
                pixels.append((rr, cc))
                if rr in (0, rows - 1) or cc in (0, cols - 1):
                    touches_edge = True
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < rows and 0 <= nc < cols and dark[nr][nc] and not seen[nr][nc]:
                            seen[nr][nc] = True
                            stack.append((nr, nc))
            if touches_edge or len(pixels) < 5:
                continue
            cy = sum(p[0] for p in pixels) / len(pixels) + y0 + 0.5
            cx = sum(p[1] for p in pixels) / len(pixels) + x0 + 0.5
            d = math.hypot(cx - px, cy - py)
            if best is None or d < best[0]:
                best = (d, cx, cy)
    return (best[1], best[2]) if best else None
