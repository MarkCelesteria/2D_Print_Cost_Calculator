import numpy as np
from PIL import Image, ImageFilter

TRANSPARENT = "transparent"
WORK_PX = 1000
MAX_EXPORT_PX = 16_000_000    # the edited picture keeps its own size up to this many pixels (16 MP)
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
BACKGROUNDS = [("White", "#ffffff"), ("Blue", "#2f6fb5"), ("Red", "#d32f2f")]
LIGHT_KEYS = ("exposure", "shadows", "highlights", "contrast", "warmth", "balance", "even")
BALANCE_EV = 1.5     # at balance = +-100 the image edges are this many stops brighter / darker
DEFAULT_SHARP = 50
LOCAL_LIGHT_EV = 1.5   # a full-strength light-brush stroke is this many stops brighter / darker
MAP_SCALE = 100        # brush maps are int8: -100..100 means -1.0..1.0
EDGE_SMOOTH_PX = 2.5   # blur (preview pixels) the edge brush's "smooth" mode removes jaggies with


def default_light():
    return {**{k: 0 for k in LIGHT_KEYS}, "gain": (1.0, 1.0, 1.0)}


def is_default_light(p):
    return all(not p.get(k) for k in LIGHT_KEYS) and tuple(p.get("gain", (1.0, 1.0, 1.0))) == (1.0, 1.0, 1.0)


def hex_to_rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def apply_lighting(arr, p, even_map=None):
    """even_map: log2-gain map from make_even_map() (any size); used when p["even"] is set."""
    if is_default_light(p):
        return arr
    x = arr.astype(np.float32) / 255.0
    warm = p.get("warmth", 0) / 100.0
    gain = np.array(p.get("gain", (1.0, 1.0, 1.0)), dtype=np.float32) * np.array(
        [1 + 0.3 * warm, 1.0, 1 - 0.3 * warm], dtype=np.float32)
    x = x * gain * (2.0 ** (p.get("exposure", 0) / 50.0))
    bal = p.get("balance", 0)
    if bal:
        # Left/right light correction: a smooth brightness ramp across the width. Positive = right side
        # brighter, left darker. It depends on relative position only, so preview and export match.
        ramp = 2.0 ** (bal / 100.0 * BALANCE_EV * np.linspace(-1.0, 1.0, x.shape[1], dtype=np.float32))
        x = x * ramp[None, :, None]
    ev = p.get("even", 0)
    if ev and even_map is not None:
        m = even_map if even_map.shape == x.shape[:2] else _resize_f(even_map, (x.shape[1], x.shape[0]))
        x = x * (2.0 ** (ev / 100.0 * m))[..., None]
        knee = np.where(x > 0.8, 0.8 + 0.2 * np.tanh((x - 0.8) / 0.2), x)    # soft ceiling: lifted skin must not blow out
        mix = np.clip(np.abs(m) * (abs(ev) / 100.0) / 0.03, 0.0, 1.0)[..., None]   # only where light was changed
        x = x + (knee - x) * mix
    lum = np.clip(x @ LUMA, 0.0, 1.0)
    lift = (p.get("shadows", 0) / 100.0) * 0.35 * (1 - lum) ** 2 + (p.get("highlights", 0) / 100.0) * 0.35 * lum ** 2
    x = x + lift[..., None]
    x = (x - 0.5) * (1 + 0.6 * p.get("contrast", 0) / 100.0) + 0.5
    return (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _map_f(m, shape):
    """int8 brush map -> float -1..1 at `shape` (h, w); resized when the picture is a different size."""
    f = m.astype(np.float32) / MAP_SCALE
    if f.shape != tuple(shape):
        f = _resize_f(f, (shape[1], shape[0]))
    return f


def apply_local_light(arr, light_map):
    """Light-brush layer, applied after the global lighting. light_map: int8, + = brighter, - = darker.
    Works on any crop or size, so the brush can repaint just the part it touched."""
    if light_map is None or not light_map.any():
        return arr
    m = _map_f(light_map, arr.shape[:2])
    x = arr.astype(np.float32) / 255.0 * (2.0 ** (m * LOCAL_LIGHT_EV))[..., None]
    knee = np.where(x > 0.8, 0.8 + 0.2 * np.tanh((x - 0.8) / 0.2), x)     # lifted skin must not blow out
    mix = np.clip(m * 20.0, 0.0, 1.0)[..., None]
    x = x + (knee - x) * mix
    return (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def paint_map(m, x, y, radius, value, softness=1.0):
    """Soft brush dab on an int8 map. value > 0 pushes up to value, value < 0 pushes down to value,
    value == 0 fades the old strokes away. Overlapping dabs don't pile up, so a stroke is even.
    Returns the (x0, y0, x1, y1) box it changed, or None."""
    h, w = m.shape
    x0, x1 = max(0, int(x - radius)), min(w, int(x + radius) + 2)
    y0, y1 = max(0, int(y - radius)), min(h, int(y + radius) + 2)
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
    f = np.clip((radius - d) / max(radius * softness, 1e-3), 0.0, 1.0)
    f = f * f * (3.0 - 2.0 * f)
    region = m[y0:y1, x0:x1].astype(np.float32)
    if value > 0:
        region = np.maximum(region, f * value)
    elif value < 0:
        region = np.minimum(region, f * value)
    else:
        region = region * (1.0 - f)
    m[y0:y1, x0:x1] = np.rint(region).astype(np.int8)
    return x0, y0, x1, y1


def light_full(arr, p, even_map=None, light_map=None, band=600):
    """apply_lighting + apply_local_light for a big picture, a band of rows at a time: the same result as
    doing it in one go, with a fraction of the memory."""
    h, w = arr.shape[:2]
    has_local = light_map is not None and bool(light_map.any())
    if is_default_light(p) and not has_local:
        return arr
    em = None
    if p.get("even") and even_map is not None:
        em = even_map if even_map.shape == (h, w) else _resize_f(even_map, (w, h))
    lm = np.rint(_map_f(light_map, (h, w)) * MAP_SCALE).astype(np.int8) if has_local else None
    out = np.empty_like(arr)
    for y in range(0, h, band):
        sl = slice(y, min(h, y + band))
        piece = apply_lighting(arr[sl], p, None if em is None else em[sl])
        out[sl] = apply_local_light(piece, None if lm is None else lm[sl])
    return out


def white_gain(rgb, target=250.0):
    s = np.maximum(np.asarray(rgb, dtype=np.float32), 1.0)
    return tuple(float(v) for v in np.clip(target / s, 0.5, 3.0))


def auto_balance(arr, alpha=None):
    """Find the left/right slider value that evens out the light on the person.
    Fits a straight line to the brightness of each column (weighted by how much subject is in it)."""
    h, w = arr.shape[:2]
    sw = 240
    sh = max(8, round(sw * h / w))
    small = np.asarray(Image.fromarray(arr[..., :3]).resize((sw, sh), Image.BOX), dtype=np.float32)
    lum = small @ LUMA / 255.0
    if alpha is not None:
        wgt = np.asarray(Image.fromarray(alpha).resize((sw, sh), Image.BOX), dtype=np.float32) / 255.0
        if wgt.sum() < 50:
            alpha = None
    if alpha is None:      # no cutout yet: look at the middle of the frame, where the person usually is
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        wgt = np.exp(-(((xx / sw - 0.5) / 0.28) ** 2 + ((yy / sh - 0.45) / 0.3) ** 2) / 2)
    col_w = wgt.sum(axis=0)
    keep = col_w > 0.08 * col_w.max()
    if keep.sum() < 8:
        return 0
    col_l = (wgt * np.log(lum + 0.03)).sum(axis=0)[keep] / col_w[keep]
    xs = np.linspace(-1.0, 1.0, sw, dtype=np.float32)[keep]
    cw = col_w[keep]
    xm, lm = np.average(xs, weights=cw), np.average(col_l, weights=cw)
    slope = float((cw * (xs - xm) * (col_l - lm)).sum() / max((cw * (xs - xm) ** 2).sum(), 1e-6))   # ln-brightness per unit x
    return int(np.clip(round(-slope / np.log(2.0) / BALANCE_EV * 100), -100, 100))


def _gauss(x, sx, sy):
    """Gaussian blur of a float image (zero outside), separable; for small images only."""
    for axis, sigma in ((1, sx), (0, sy)):
        if sigma < 0.5:
            continue
        t = np.arange(-int(3 * sigma), int(3 * sigma) + 1, dtype=np.float32)
        k = np.exp(-(t ** 2) / (2 * sigma ** 2))
        k /= k.sum()
        x = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), axis, x)
    return x


def make_even_map(arr, alpha=None):
    """Log2-gain map (1.0 = full strength) that evens out light falling unevenly across the person,
    row by row: where a row is darker than that row's average (e.g. the shadow side of the face),
    the map lifts it, and where it is brighter it pulls it down. Uses only the subject (cutout, or
    everything that is not blank white background). Computed small; resize it to any image."""
    h, w = arr.shape[:2]
    sw = 240
    sh = max(8, round(sw * h / w))
    small = np.asarray(Image.fromarray(arr[..., :3]).resize((sw, sh), Image.BOX), dtype=np.float32)
    lum = small @ LUMA / 255.0
    if alpha is not None:
        m = np.asarray(Image.fromarray(alpha).resize((sw, sh), Image.BOX), dtype=np.float32) / 255.0
    else:
        m = (lum < 0.94).astype(np.float32)
    m = m * (lum > 0.03)
    logl = np.log(lum + 0.03).astype(np.float32)
    sx, sy = 0.10 * sw, max(1.0, 0.025 * sh)
    den = _gauss(m, sx, sy)
    local = _gauss(m * logl, sx, sy) / np.maximum(den, 1e-3)        # smooth brightness around each pixel
    rw = m.sum(axis=1)
    row_mean = (m * local).sum(axis=1) / np.maximum(rw, 1e-3)
    row_max = np.where(m > 0.5, local, -9.0).max(axis=1)
    row_max = np.where(rw > 2, row_max, row_mean)
    target = row_mean + 0.3 * (row_max - row_mean)               # lift the dark side up toward the bright side
    target = _gauss(target[:, None], 0, 0.03 * sh)[:, 0] if sh > 20 else target
    gain = (target[:, None] - local) / np.log(2.0)
    gain = np.clip(gain * 1.2, -0.3, 0.9)                        # mostly lifts; only a little pull-down
    fade = np.clip((den - 0.12) / 0.3, 0.0, 1.0) * np.clip(rw / (0.15 * sw), 0.0, 1.0)[:, None]
    fade = fade * np.clip((_gauss(m, 1.0, 1.0) - 0.5) * 3.0, 0.0, 1.0)   # only on the person, never on the background or its fringe
    return (gain * fade).astype(np.float32)


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
        "balance": 0,
    }


# ---------------- cutout edge quality ----------------

def _box_mean(x, r):
    """Mean over a (2r+1) x (2r+1) window (shrinks at the borders), using running sums."""
    for axis in (0, 1):
        n = x.shape[axis]
        c = np.cumsum(x, axis=axis, dtype=np.float64)
        zero_shape = list(x.shape)
        zero_shape[axis] = 1
        c = np.concatenate([np.zeros(zero_shape, dtype=np.float64), c], axis=axis)
        idx = np.arange(n)
        hi, lo = np.minimum(idx + r + 1, n), np.maximum(idx - r, 0)
        shape = [1] * x.ndim
        shape[axis] = n
        x = (np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)) / (hi - lo).reshape(shape)
    return x


def _resize_f(a, size):
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize(size, Image.BILINEAR), dtype=np.float32)


def _minmax(a, r):
    """Local minimum and maximum over a (2r+1) x (2r+1) window (separable, edge-padded)."""
    lo = hi = a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        lp, hp = np.pad(lo, pad, mode="edge"), np.pad(hi, pad, mode="edge")
        n = a.shape[axis]

        def cut(k):
            return tuple(slice(k, k + n) if i == axis else slice(None) for i in (0, 1))
        lo, hi = lp[cut(0)], hp[cut(0)]
        for k in range(1, 2 * r + 1):
            lo, hi = np.minimum(lo, lp[cut(k)]), np.maximum(hi, hp[cut(k)])
    return lo, hi


def refine_alpha(rgb, alpha, radius, eps=1e-3, sub=1):
    """Make a soft / low-resolution matte follow the real edges of the photo (guided filter).
    rgb: HxWx3 uint8, alpha: HxW float 0..1 at the same size. radius is in pixels of this image.
    sub > 1 solves on a smaller copy and upsamples the result: much faster on big images, same look."""
    h, w = alpha.shape
    guide = rgb[..., :3].astype(np.float32) @ LUMA / 255.0
    if sub > 1:
        size = (max(1, w // sub), max(1, h // sub))
        g, p = _resize_f(guide, size), _resize_f(alpha, size)
    else:
        g, p = guide, alpha.astype(np.float32)
    r = max(1, int(round(radius / sub)))
    mg, mp = _box_mean(g, r), _box_mean(p, r)
    var_g = _box_mean(g * g, r) - mg * mg
    cov = _box_mean(g * p, r) - mg * mp
    a = cov / (var_g + eps)
    b = mp - a * mg
    ma, mb = _box_mean(a, r), _box_mean(b, r)
    lo, hi = _minmax(p.astype(np.float32), max(1, r // 2))
    if sub > 1:
        ma, mb = _resize_f(ma, (w, h)), _resize_f(mb, (w, h))
        lo, hi = _resize_f(lo, (w, h)), _resize_f(hi, (w, h))
    q = ma * guide + mb
    # The photo may only move the edge inside the matte's own soft band. Solid areas stay solid, so
    # wrinkles, shadows and watermark text on the shirt can't punch holes or grow spikes.
    return np.clip(q, lo, hi).astype(np.float32)


def _blur(a, sigma):
    img = Image.fromarray((np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32) / 255.0


def interior_mask(alpha_u8):
    """1 inside the person where the matte is unsure (e.g. a white shirt on a white wall), 0 near the outline.
    "Outside" = everything that can be reached from the picture border through see-through pixels; the unsure
    areas that are NOT connected to it are inside the person, so they must not be cut away."""
    h, w = alpha_u8.shape
    k = 256.0 / max(h, w)
    size = (max(8, round(w * k)), max(8, round(h * k)))
    small = np.asarray(Image.fromarray(alpha_u8).resize(size, Image.BOX), dtype=np.float32) / 255.0
    low = (small < 0.3).astype(np.uint8) * 255
    seed = np.zeros_like(low)
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = low[0, :], low[-1, :], low[:, 0], low[:, -1]
    if not seed.any():
        return np.zeros((h, w), np.float32)
    cur = seed
    for _ in range(600):                      # flood fill from the border, a pixel ring at a time
        nxt = np.minimum(np.asarray(Image.fromarray(cur).filter(ImageFilter.MaxFilter(3))), low)
        if np.array_equal(nxt, cur):
            break
        cur = nxt
    near = np.asarray(Image.fromarray(cur).filter(ImageFilter.MaxFilter(7))) > 0     # the outline band
    prot = (~near).astype(np.float32)
    return np.clip(_resize_f(prot, (w, h)), 0.0, 1.0)


def _edge_curve(a, shrink, sharp, protect):
    t = 0.5 + 0.05 * shrink
    k = max(1.0 + 0.07 * sharp, 0.5 / (1.0 - t))
    crisp = np.clip((np.clip((a - 0.12) / 0.78, 0.0, 1.0) - t) * k + 0.5, 0.0, 1.0)   # also drops the faint fringe
    if protect is not None:      # inside the person: unsure areas may only get more solid, never be cut away
        crisp = crisp * (1.0 - protect) + np.maximum(a, crisp) * protect
    return crisp


def finish_alpha(alpha, feather, shrink, sharp=DEFAULT_SHARP, scale=1.0, protect=None, local=None):
    """Turn the stored matte into the final one. Works on any size and on crops.
    `scale` = this image's size relative to the preview image, so the look matches.
    `protect` = interior_mask() for the same pixels (or None): unsure areas inside the person are left alone.
    `local` = edge-brush map (int8, same pixels or any size) or None:
      + = harder edge there, - = smoother outline there (blur first, then re-sharpen, so the contour
      loses its jaggies but the edge stays crisp).
      sharp  0..100  how hard the edge is
      shrink 0..5    pulls the edge inwards (removes the old-background fringe)
      feather        blur radius in preview pixels, applied last so the edge stays anti-aliased"""
    a = alpha.astype(np.float32) / 255.0 if alpha.dtype == np.uint8 else alpha.astype(np.float32)
    a = _blur(a, 0.8 * scale)                  # smooths pixel-size spikes before the edge is tightened
    crisp = _edge_curve(a, shrink, sharp, protect)
    if local is not None and local.any():
        lm = _map_f(local, a.shape)
        pos, neg = np.clip(lm, 0.0, 1.0), np.clip(-lm, 0.0, 1.0)
        if pos.any():
            crisp = crisp + (_edge_curve(a, shrink, 150, protect) - crisp) * pos
        if neg.any():
            smooth = _edge_curve(_blur(a, EDGE_SMOOTH_PX * scale), shrink, max(sharp, 70), protect)
            crisp = crisp + (smooth - crisp) * neg
    a = crisp
    sigma = feather * scale
    if sigma > 0.05:
        a = _blur(a, sigma)
    return a


def refine_matte(rgb, alpha_u8):
    """Run once on the raw model output (at preview size)."""
    h, w = alpha_u8.shape
    alpha_u8 = np.asarray(Image.fromarray(alpha_u8).filter(ImageFilter.MedianFilter(5)))   # drops spikes and specks
    a = refine_alpha(rgb, alpha_u8.astype(np.float32) / 255.0, radius=max(2, 0.006 * max(h, w)))
    return (a * 255.0 + 0.5).astype(np.uint8)


def export_alpha(rgb_full, alpha_u8, feather, shrink, sharp, edge_map=None):
    """Matte for the full-size photo: upsample the stored matte, snap it to the full-resolution edges,
    then apply the same finishing as the preview."""
    h, w = rgb_full.shape[:2]
    scale = max(h, w) / max(alpha_u8.shape)
    prot = interior_mask(alpha_u8)
    if prot.shape != (h, w):
        prot = _resize_f(prot, (w, h))
    up = np.asarray(Image.fromarray(alpha_u8).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0
    if scale > 1.05:
        up = refine_alpha(rgb_full, np.clip(up, 0, 1), radius=max(2, 0.006 * max(h, w)), sub=max(1, round(scale)))
    return finish_alpha(up, feather, shrink, sharp, scale, prot, edge_map)


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


def compose_transparent(rgb, alpha, clean=True):
    h, w = rgb.shape[:2]
    if alpha.shape != (h, w):
        alpha = np.clip(np.asarray(Image.fromarray(alpha).resize((w, h), Image.BICUBIC), dtype=np.float32), 0.0, 1.0)
    fg = _decontaminate(rgb, alpha) if clean else rgb.astype(np.float32)
    out = np.concatenate([np.clip(fg, 0.0, 255.0), alpha[..., None] * 255.0], axis=2)
    return (out + 0.5).astype(np.uint8)


def over_checker(rgba, tile=12, origin=(0, 0)):
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    xx, yy = xx + origin[0], yy + origin[1]
    shade = np.where(((xx // tile) + (yy // tile)) % 2 == 0, 255.0, 225.0)[..., None]
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return ((rgba[..., :3] * a + shade * (1 - a)) + 0.5).astype(np.uint8)


def compose(rgb, alpha, bg, clean=True):
    h, w = rgb.shape[:2]
    if alpha.shape != (h, w):
        alpha = np.clip(np.asarray(Image.fromarray(alpha).resize((w, h), Image.BICUBIC), dtype=np.float32), 0.0, 1.0)
    fg = _decontaminate(rgb, alpha) if clean else rgb.astype(np.float32)
    a = alpha[..., None]
    out = fg * a + np.array(bg, dtype=np.float32) * (1 - a)
    return (np.clip(out, 0.0, 255.0) + 0.5).astype(np.uint8)


def paint(alpha, x, y, radius, value, softness=0.35):
    """Brush dab on the uint8 matte. Returns the (x0, y0, x1, y1) box it changed, or None."""
    h, w = alpha.shape
    x0, x1 = max(0, int(x - radius)), min(w, int(x + radius) + 2)
    y0, y1 = max(0, int(y - radius)), min(h, int(y + radius) + 2)
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
    m = np.clip((radius - d) / max(radius * softness, 1e-3), 0.0, 1.0)
    region = alpha[y0:y1, x0:x1].astype(np.float32)
    region = np.maximum(region, m * 255.0) if value > 127 else np.minimum(region, (1 - m) * 255.0)
    alpha[y0:y1, x0:x1] = region.astype(np.uint8)
    return x0, y0, x1, y1