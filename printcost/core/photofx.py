import numpy as np
from PIL import Image, ImageFilter

WORK_PX = 1000
MAX_EXPORT = 3000
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
BACKGROUNDS = [("White", "#ffffff"), ("Blue", "#2f6fb5"), ("Red", "#d32f2f")]
LIGHT_KEYS = ("exposure", "shadows", "highlights", "contrast", "warmth")


def default_light():
    return {**{k: 0 for k in LIGHT_KEYS}, "gain": (1.0, 1.0, 1.0)}


def is_default_light(p):
    return all(not p.get(k) for k in LIGHT_KEYS) and tuple(p.get("gain", (1.0, 1.0, 1.0))) == (1.0, 1.0, 1.0)


def hex_to_rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def apply_lighting(arr, p):
    if is_default_light(p):
        return arr
    x = arr.astype(np.float32) / 255.0
    warm = p.get("warmth", 0) / 100.0
    gain = np.array(p.get("gain", (1.0, 1.0, 1.0)), dtype=np.float32) * np.array(
        [1 + 0.3 * warm, 1.0, 1 - 0.3 * warm], dtype=np.float32)
    x = x * gain * (2.0 ** (p.get("exposure", 0) / 50.0))
    lum = np.clip(x @ LUMA, 0.0, 1.0)
    lift = (p.get("shadows", 0) / 100.0) * 0.35 * (1 - lum) ** 2 + (p.get("highlights", 0) / 100.0) * 0.35 * lum ** 2
    x = x + lift[..., None]
    x = (x - 0.5) * (1 + 0.6 * p.get("contrast", 0) / 100.0) + 0.5
    return (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def white_gain(rgb, target=250.0):
    s = np.maximum(np.asarray(rgb, dtype=np.float32), 1.0)
    return tuple(float(v) for v in np.clip(target / s, 0.5, 3.0))


def auto_lighting(arr, alpha=None):
    lum = arr[..., :3].astype(np.float32) @ LUMA / 255.0
    sel = lum[alpha > 127] if alpha is not None else lum.ravel()
    if sel.size < 500:
        sel = lum.ravel()
    sel = sel[::max(1, sel.size // 100000)]
    lo, ref, hi = np.percentile(sel, (2, 75, 98))
    ev = float(np.clip(np.log2(0.62 / max(ref, 0.04)), -1.0, 1.0))
    k = 2.0 ** ev
    lo, hi = lo * k, hi * k
    return {
        "exposure": round(ev * 50),
        "shadows": round(float(np.clip((0.12 - lo) * 350, 0, 70))),
        "highlights": -round(float(np.clip((hi - 0.92) * 500, 0, 70))),
        "contrast": round(float(np.clip((0.8 - (hi - lo)) * 100, 0, 30))),
        "warmth": 0,
    }


def finish_alpha(alpha, feather, shrink):
    img = Image.fromarray(alpha)
    if shrink > 0:
        img = img.filter(ImageFilter.MinFilter(2 * int(shrink) + 1))
    if feather > 0:
        img = img.filter(ImageFilter.GaussianBlur(feather))
    return np.asarray(img, dtype=np.float32) / 255.0


def _decontaminate(rgb, alpha):
    h, w = alpha.shape
    sigma = max(2.0, 3.0 * max(h, w) / WORK_PX)
    core = (alpha > 0.97).astype(np.uint8) * 255
    weight = np.asarray(Image.fromarray(core).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32) / 255.0
    prem = (rgb * (core[..., None] > 0)).astype(np.uint8)
    blurred = np.asarray(Image.fromarray(prem).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32)
    est = np.minimum(blurred / np.maximum(weight, 1e-3)[..., None], 255.0)
    use = np.clip((0.97 - alpha) / 0.07, 0.0, 1.0) * (weight > 0.03)
    fg = rgb.astype(np.float32)
    return fg + (est - fg) * use[..., None]


def compose(rgb, alpha, bg, clean=True):
    h, w = rgb.shape[:2]
    if alpha.shape != (h, w):
        alpha = np.clip(np.asarray(Image.fromarray(alpha).resize((w, h), Image.BICUBIC), dtype=np.float32), 0.0, 1.0)
    fg = _decontaminate(rgb, alpha) if clean else rgb.astype(np.float32)
    a = alpha[..., None]
    out = fg * a + np.array(bg, dtype=np.float32) * (1 - a)
    return (np.clip(out, 0.0, 255.0) + 0.5).astype(np.uint8)


def paint(alpha, x, y, radius, value, softness=0.35):
    h, w = alpha.shape
    x0, x1 = max(0, int(x - radius)), min(w, int(x + radius) + 2)
    y0, y1 = max(0, int(y - radius)), min(h, int(y + radius) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
    m = np.clip((radius - d) / max(radius * softness, 1e-3), 0.0, 1.0)
    region = alpha[y0:y1, x0:x1].astype(np.float32)
    region = np.maximum(region, m * 255.0) if value > 127 else np.minimum(region, (1 - m) * 255.0)
    alpha[y0:y1, x0:x1] = region.astype(np.uint8)