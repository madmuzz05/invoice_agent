"""Deteksi area teks (mis. tiap struk dalam satu foto) tanpa LLM.

Dipakai sebelum OCR halaman hasil scan/foto: tiap area di-OCR sendiri supaya
teks beberapa struk yang berdampingan tidak tercampur per baris.

Cara kerja (numpy saja):
1. Gambar diperkecil (sisi terpanjang ~400 px) dengan min-pool agar tinta tipis
   tetap terlihat.
2. Tinta = piksel yang lebih gelap dari rata-rata lokalnya (tahan terhadap
   pencahayaan tidak rata).
3. Goresan yang bukan teks dibuang: garis/tepi kertas (tipis memanjang, juga
   yang miring), bintik, dan blok besar.
4. XY-cut: area dipotong rekursif di celah kosong terlebar (kolom/baris)
   berdasarkan proyeksi jumlah tinta. Celah antar-struk lebih lebar dari celah
   antar-baris, sehingga tiap struk menjadi satu kotak; noise kecil tidak
   menyambungkan struk karena hanya menambah sedikit nilai proyeksi.
5. Kotak kecil (logo/header yang terpisah) digabung ke kotak besar terdekat.
"""

from __future__ import annotations

from collections import deque

import numpy as np

_WORK_SIZE = 400          # sisi terpanjang gambar kerja (px)
_LOCAL_WINDOW = 8         # radius jendela rata-rata lokal (px kerja)
# Selisih kecerahan minimum terhadap rata-rata lokal (bayangan tepi kertas < 30)
_INK_CONTRAST = 30
# Celah minimum (px kerja) untuk memotong: antar-kolom struk ~11-15 px,
# antar-struk vertikal ~30 px, celah antar-baris di dalam struk ≤ 10 px.
_MIN_GAP_X, _MIN_GAP_Y = 6, 14
_MIN_REGION_FRACTION = 0.01
_MAX_REGIONS = 12


def _box_sum(values: np.ndarray, half_h: int, half_w: int) -> np.ndarray:
    """Jumlah nilai dalam jendela (2*half_h+1) x (2*half_w+1) per piksel."""
    height, width = values.shape
    integral = np.zeros((height + 1, width + 1))
    integral[1:, 1:] = values.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(height) - half_h, 0, height)
    y1 = np.clip(np.arange(height) + half_h + 1, 0, height)
    x0 = np.clip(np.arange(width) - half_w, 0, width)
    x1 = np.clip(np.arange(width) + half_w + 1, 0, width)
    return integral[y1][:, x1] - integral[y0][:, x1] - integral[y1][:, x0] + integral[y0][:, x0]


def _components(mask: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int, int]]]:
    """Komponen terhubung (4-arah). Returns (label per piksel, [(x0,y0,x1,y1,jumlah)])."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    boxes: list[tuple[int, int, int, int, int]] = []
    for y in range(height):
        for x in np.flatnonzero(mask[y] & (labels[y] == 0)):
            if labels[y, x]:
                continue
            label = len(boxes) + 1
            labels[y, x] = label
            queue = deque([(y, int(x))])
            top, bottom, left, right, count = y, y, int(x), int(x), 0
            while queue:
                cy, cx = queue.popleft()
                count += 1
                top, bottom = min(top, cy), max(bottom, cy)
                left, right = min(left, cx), max(right, cx)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = label
                        queue.append((ny, nx))
            boxes.append((left, top, right + 1, bottom + 1, count))
    return labels, boxes


def _area(box: tuple[float, ...]) -> float:
    return max(box[2] - box[0], 0) * max(box[3] - box[1], 0)


def _gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0)


def _merge_small(boxes: list[tuple[float, ...]], near: float) -> list[tuple[float, ...]]:
    """Kotak kecil (<25% kotak besar) yang menempel/tumpang tindih digabung."""
    boxes = sorted(boxes, key=_area, reverse=True)
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes) - 1, 0, -1):
            small = boxes[i]
            candidates = [
                j for j in range(i)
                if _area(small) < 0.25 * _area(boxes[j]) and _gap(small, boxes[j]) <= near
            ]
            if candidates:
                j = min(candidates, key=lambda k: _gap(small, boxes[k]))
                big = boxes[j]
                boxes[j] = (
                    min(big[0], small[0]), min(big[1], small[1]),
                    max(big[2], small[2]), max(big[3], small[3]),
                )
                del boxes[i]
                merged = True
                break
    return boxes


def _gaps(profile: np.ndarray, noise: float, min_gap: int) -> list[tuple[int, int]]:
    """Rentang indeks kosong (nilai ≤ noise) sepanjang ≥ min_gap, bukan di tepi."""
    empty = profile <= noise
    gaps, start = [], None
    for index, is_empty in enumerate(empty):
        if is_empty and start is None:
            start = index
        elif not is_empty and start is not None:
            if start > 0 and index - start >= min_gap:
                gaps.append((start, index))
            start = None
    return gaps


def _xy_cut(
    text: np.ndarray, x0: int, y0: int, x1: int, y1: int, depth: int = 0
) -> list[tuple[int, int, int, int]]:
    """Potong rekursif di celah kosong terlebar (kolom atau baris). Toleran
    terhadap noise kecil karena memakai proyeksi jumlah tinta, bukan
    keterhubungan piksel."""
    block = text[y0:y1, x0:x1]
    rows, cols = np.flatnonzero(block.any(1)), np.flatnonzero(block.any(0))
    if rows.size == 0 or cols.size == 0:
        return []
    y0, y1 = y0 + int(rows[0]), y0 + int(rows[-1]) + 1
    x0, x1 = x0 + int(cols[0]), x0 + int(cols[-1]) + 1
    block = text[y0:y1, x0:x1]
    if depth >= 8:
        return [(x0, y0, x1, y1)]

    h, w = block.shape
    col_profile, row_profile = block.sum(0), block.sum(1)
    col_gaps = _gaps(col_profile, max(1.0, 0.01 * h), _MIN_GAP_X)
    row_gaps = _gaps(row_profile, max(1.0, 0.01 * w), _MIN_GAP_Y)
    best = None
    for start, end in col_gaps:
        score = (end - start) / _MIN_GAP_X
        if best is None or score > best[0]:
            best = (score, "x", (start + end) // 2)
    for start, end in row_gaps:
        score = (end - start) / _MIN_GAP_Y
        if best is None or score > best[0]:
            best = (score, "y", (start + end) // 2)
    if best is None:
        return [(x0, y0, x1, y1)]
    _, axis, cut = best
    if axis == "x":
        return _xy_cut(text, x0, y0, x0 + cut, y1, depth + 1) + _xy_cut(
            text, x0 + cut, y0, x1, y1, depth + 1
        )
    return _xy_cut(text, x0, y0, x1, y0 + cut, depth + 1) + _xy_cut(
        text, x0, y0 + cut, x1, y1, depth + 1
    )


def detect_text_regions(gray: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Kotak area teks (x0, y0, x1, y1) dalam koordinat piksel `gray`, urut baca
    (atas→bawah, kiri→kanan). List kosong jika tidak ada/terlalu banyak area."""
    factor = max(int(np.ceil(max(gray.shape) / _WORK_SIZE)), 1)
    height, width = gray.shape[0] // factor, gray.shape[1] // factor
    if height < 20 or width < 20:
        return []
    small = gray[: height * factor, : width * factor].reshape(
        height, factor, width, factor
    ).min(axis=(1, 3)).astype(float)

    ones = np.ones_like(small)
    local_mean = _box_sum(small, _LOCAL_WINDOW, _LOCAL_WINDOW) / _box_sum(
        ones, _LOCAL_WINDOW, _LOCAL_WINDOW
    )
    ink = small < local_mean - _INK_CONTRAST
    ink[:3, :] = ink[-3:, :] = False
    ink[:, :3] = ink[:, -3:] = False

    labels, strokes = _components(ink)
    keep = np.zeros(len(strokes) + 1, dtype=bool)
    for index, (x0, y0, x1, y1, count) in enumerate(strokes, start=1):
        w, h = x1 - x0, y1 - y0
        long_side, short_side = max(w, h), max(min(w, h), 1)
        is_line = long_side >= 12 and (long_side / short_side >= 5 or count < 0.25 * w * h)
        keep[index] = count >= 2 and not is_line and long_side <= 40
    text = keep[labels]

    min_area = _MIN_REGION_FRACTION * height * width
    boxes = [
        (float(x0 * factor), float(y0 * factor), float(x1 * factor), float(y1 * factor))
        for x0, y0, x1, y1 in _xy_cut(text, 0, 0, width, height)
        if (x1 - x0) * (y1 - y0) >= min_area
    ]
    boxes = _merge_small(boxes, near=10 * factor)
    if len(boxes) > _MAX_REGIONS:
        return []
    band = max(gray.shape) / 8
    return sorted(boxes, key=lambda b: (int(b[1] // band), b[0]))


# ── Deteksi teks yang diputar 90° (mis. struk ditempel menyamping) ─────────────

_ORIENT_WORK_SIZE = 600
_ORIENT_TILE, _ORIENT_STEP = 60, 30
_MIN_SIDEWAYS_TILES = 10


def _orientation_ink(gray: np.ndarray) -> tuple[np.ndarray, int]:
    factor = max(int(np.ceil(max(gray.shape) / _ORIENT_WORK_SIZE)), 1)
    height, width = gray.shape[0] // factor, gray.shape[1] // factor
    small = gray[: height * factor, : width * factor].reshape(
        height, factor, width, factor
    ).min(axis=(1, 3)).astype(float)
    ones = np.ones_like(small)
    local_mean = _box_sum(small, _LOCAL_WINDOW, _LOCAL_WINDOW) / _box_sum(
        ones, _LOCAL_WINDOW, _LOCAL_WINDOW
    )
    return small < local_mean - _INK_CONTRAST, factor


def detect_sideways_text(gray: np.ndarray) -> tuple[float, float, float, float] | None:
    """Kotak (koordinat piksel `gray`) area terbesar yang teksnya berjalan
    vertikal (diputar 90°), atau None. Tanpa model: tiap tile dinilai dari
    profil tinta — baris teks horizontal membuat profil per-baris bergelombang,
    teks vertikal membuat profil per-kolom bergelombang."""
    ink, factor = _orientation_ink(gray)
    height, width = ink.shape
    tile, step = _ORIENT_TILE, _ORIENT_STEP
    vertical: set[tuple[int, int]] = set()
    for y in range(0, height - tile + 1, step):
        for x in range(0, width - tile + 1, step):
            block = ink[y:y + tile, x:x + tile].astype(float)
            if block.mean() < 0.03:
                continue
            rows, cols = block.sum(1), block.sum(0)
            row_wave = rows.std() / (rows.mean() + 1e-6)
            col_wave = cols.std() / (cols.mean() + 1e-6)
            if col_wave > 1.3 * row_wave and col_wave > 0.8:
                vertical.add((x, y))

    best: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for start in vertical:
        if start in seen:
            continue
        seen.add(start)
        queue, cluster = deque([start]), []
        while queue:
            x, y = queue.popleft()
            cluster.append((x, y))
            for nxt in ((x + step, y), (x - step, y), (x, y + step), (x, y - step)):
                if nxt in vertical and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if len(cluster) > len(best):
            best = cluster
    if len(best) < _MIN_SIDEWAYS_TILES:
        return None
    x0 = min(x for x, _ in best)
    y0 = min(y for _, y in best)
    x1 = max(x for x, _ in best) + tile
    y1 = max(y for _, y in best) + tile
    return (float(x0 * factor), float(y0 * factor), float(x1 * factor), float(y1 * factor))
