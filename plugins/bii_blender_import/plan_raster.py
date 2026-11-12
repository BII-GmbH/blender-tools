"""Load plans (PDF, TIFF, PNG, JPEG) as images with their resolution. No bpy.

PDF pages are rendered with PDFium (pypdfium2). A scanned PDF holds one
large image; the page is rendered at that image's resolution, so no detail
is lost or invented. Other formats are read with Pillow, which also reads
the 1-bit CCITT compressed TIFFs of scanned plans.

The result is written as PNG for Blender, with the plan's own colors. Its
alpha marks the white paper for Solid view.
"""

import contextlib
import io
import math
import os

PDF_EXTENSIONS = {".pdf"}
RASTER_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}

# Pixels on the long side of a rendered PDF page. GPUs show no more, so
# Blender would scale a larger image down for the viewport anyway.
MAX_SIDE = 16384
PAPER_LEVEL = 230  # pixels this light in every channel count as paper


class Sheet:
    """A loaded plan: Pillow image and its resolution in pixels per inch.

    note: a remark for the import report; warning: the note asks the user to
    check something (a guessed resolution, say).
    """

    def __init__(self, image, dpi_x, dpi_y, note="", warning=False):
        self.image = image
        self.dpi_x = dpi_x
        self.dpi_y = dpi_y
        self.note = note
        self.warning = warning

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

    Returns the dpi, or None for pages without such an image (vector
    drawings). The image matrix maps the image's unit square onto the page,
    so its column lengths are the drawn size in points.
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
        best = dpi if best is None else max(best, dpi)
    return best


def load_pdf(path, page_index=0, dpi=0.0):
    """Render one PDF page. dpi 0 means the scan's own resolution, else 300.

    The page is rendered in color even for gray scans: stamps or revision
    clouds drawn over the scan may be colored. write_png stores gray
    results as grayscale.
    """
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        if not 0 <= page_index < len(pdf):
            raise ValueError(f"The PDF has {len(pdf)} pages, there is no page {page_index + 1}")
        page = pdf[page_index]
        page_w, page_h = page.get_size()
        scan_dpi = _scan_resolution(page)
        note = ""
        if dpi <= 0:
            dpi = round(scan_dpi) if scan_dpi else 300.0
            note = f"scanned at {dpi:.0f} dpi" if scan_dpi else "vector page, rendered at 300 dpi"
        long_side = max(page_w, page_h) / 72 * dpi
        if long_side > MAX_SIDE:
            # Slightly less, as the pixel size is rounded up.
            dpi *= MAX_SIDE / long_side * (1 - 1e-6)
            note = (f"rendered at {dpi:.0f} dpi, {MAX_SIDE} pixels on the long side: GPUs "
                    "show no more")
        bitmap = page.render(scale=dpi / 72)
        image = bitmap.to_pil()
        # The real resolution after rounding the pixel size.
        return Sheet(image, image.width / (page_w / 72), image.height / (page_h / 72), note)
    finally:
        pdf.close()


@contextlib.contextmanager
def _large_images():
    """Let Pillow open plans larger than its decompression bomb limit.

    Plans are large on purpose. The limit is Pillow's global setting, shared
    with every other add-on in Blender, so it is lifted only meanwhile.
    """
    from PIL import Image
    limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def load_raster(path):
    from PIL import Image
    with _large_images():
        image = Image.open(path)
        image.seek(0)
        image.load()
    dpi = image.info.get("dpi")
    if not dpi or not dpi[0] or not dpi[1]:
        return Sheet(image, 300.0, 300.0,
                     "no resolution in the file, 300 dpi assumed: check the scale",
                     warning=True)
    return Sheet(image, float(dpi[0]), float(dpi[1]))


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


def _on_white(image):
    """The image as "L" or "RGB", with transparent parts laid on white paper.

    Plans exported from CAD often have a transparent background whose
    color is black; dropping the alpha would turn the paper black.
    """
    from PIL import Image
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode in ("1", "I", "I;16", "F"):
        image = image.convert("L")
    if image.mode in ("L", "RGB"):
        return image
    if image.mode in ("LA", "La", "PA", "RGBA", "RGBa"):
        rgba = image.convert("RGBA")
        paper = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        paper.alpha_composite(rgba)
        return paper.convert("RGB")
    return image.convert("RGB")  # CMYK, YCbCr, ...


def write_png(sheet, path):
    """Write the sheet as PNG. Returns a short description of the format.

    The colors stay as they are; the shader uses them unchanged. The alpha
    only marks the paper (pixels this light in every channel) for Solid
    view, which cuts the image off at its alpha.
    """
    from PIL import Image, ImageChops
    image = _on_white(sheet.image)
    if image.mode == "RGB":
        r, g, b = image.split()
        if (ImageChops.difference(r, g).getbbox() is None
                and ImageChops.difference(g, b).getbbox() is None):
            image = r  # gray, stored with one channel
            lowest = r
        else:
            lowest = ImageChops.darker(ImageChops.darker(r, g), b)
        del r, g, b
    else:
        lowest = image
    if image.mode == "L" and _is_black_and_white(image):
        # 1-bit palette, white transparent.
        index = image.point(lambda v: 1 if v >= 128 else 0)
        pal = Image.frombytes("P", image.size, index.tobytes())
        pal.putpalette([0, 0, 0, 255, 255, 255])
        pal.save(path, transparency=bytes([255, 0]), bits=1, optimize=True)
        return "black and white"
    alpha = lowest.point(lambda v: 0 if v >= PAPER_LEVEL else 255)
    if image.mode == "L":
        Image.merge("LA", (image, alpha)).save(path)
        return "grayscale"
    image.putalpha(alpha)
    image.save(path)
    return "color"


def gray_pixels(data_or_path):
    """Grayscale pixels (numpy uint8, row 0 at the top) of a PNG file or bytes.

    The alpha is ignored: it only marks pixels as light as paper, whose
    colors stay in the PNG, and snapping looks for dark pixels.
    """
    import numpy as np
    from PIL import Image
    source = io.BytesIO(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) else data_or_path
    with _large_images(), Image.open(source) as image:
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
