#!/usr/bin/env python3
"""Konwerter multimediow. Obrazy (JPG, PNG, WEBP, BMP, TIFF, GIF, ICO; wejscie takze
HEIC z telefonow) i wideo (MP4, WebM, MKV, AVI, GIF, MP3) do formatow czesto uzywanych
na Windows i Androidzie. Wszystko lokalnie: obrazy robi Pillow, wideo ffmpeg
(dolaczony do pakietu imageio-ffmpeg), pliki nie sa nigdzie wysylane.

Uruchomienie: python konwerter.py            (GUI)
              python konwerter.py --selftest (test logiki)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(APP_DIR, "ustawienia.json")  # ostatnie wybory i rozmiar okna

IMG_IN = (".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".ico",
          ".heic", ".heif", ".avif")
VID_IN = (".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".3gp", ".mpg", ".mpeg",
          ".ts", ".mts", ".m2ts")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # bez migajacego okna konsoli przy ffmpeg

# nazwa: (rozszerzenie, format Pillow, opis w oknie)
IMAGE_OUT = {
    "JPG": (".jpg", "JPEG", "zdjecia, najczesciej uzywany"),
    "PNG": (".png", "PNG", "bez strat, z przezroczystoscia"),
    "WEBP": (".webp", "WEBP", "maly plik, Android i internet"),
    "BMP": (".bmp", "BMP", "bez kompresji"),
    "TIFF": (".tif", "TIFF", "skany i druk"),
    "GIF": (".gif", "GIF", "256 kolorow"),
    "ICO": (".ico", "ICO", "ikona Windows, maks. 256 px"),
}
# nazwa: (rozszerzenie, opis w oknie)
VIDEO_OUT = {
    "MP4": (".mp4", "H.264 + AAC: Windows, Android, telewizory"),
    "WEBM": (".webm", "VP9 + Opus: internet, mniejszy plik, wolniej"),
    "MKV": (".mkv", "H.264 + AAC"),
    "AVI": (".avi", "MPEG-4 + MP3: starsze odtwarzacze"),
    "GIF": (".gif", "animacja bez dzwieku (krotkie fragmenty)"),
    "MP3": (".mp3", "sam dzwiek z wideo"),
}
# jakosc: (JPEG/WebP, crf x264, q MPEG-4, q MP3); VP9 = crf + 8
QUALITY = {"Wysoka": (95, 19, 2, 0), "Dobra": (85, 23, 4, 2), "Mala (maly plik)": (60, 28, 8, 5)}
SIZES = {"obraz": ("Oryginal", "3840", "2560", "1920", "1280", "1024", "800", "512"),
         "wideo": ("Oryginal", "2160", "1440", "1080", "720", "480", "360")}

_proc = None  # biezacy ffmpeg (okno zabija go przy zamknieciu)

# ------------------------------------------------------------------ pliki ----


def kind_of(path):
    e = os.path.splitext(path)[1].lower()
    return "obraz" if e in IMG_IN else "wideo" if e in VID_IN else None


def matches(path, kind):
    """Czy plik nadaje sie do konwersji danego rodzaju (GIF mozna tez zamienic na wideo)."""
    e = os.path.splitext(path)[1].lower()
    return e in IMG_IN if kind == "obraz" else e in VID_IN + (".gif",)


def list_inputs(inp, exts):
    """Plik, folder albo lista plikow/folderow -> lista plikow (z folderu: tylko exts, alfabetycznie)."""
    files = []
    for p in inp if isinstance(inp, (list, tuple)) else [inp]:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(exts))
        else:
            files.append(p)
    return files


def unique(path):
    """Wolna nazwa pliku: a.jpg, a_2.jpg, ... (nigdy nie nadpisuje)."""
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(path):
        path, k = "%s_%d%s" % (base, k, ext), k + 1
    return path


def fsize(path):
    n = os.path.getsize(path)
    return "%.1f MB" % (n / 1048576) if n >= 1048576 else "%.0f KB" % (n / 1024)


def load_settings(path=SETTINGS):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data, path=SETTINGS):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:  # np. folder tylko do odczytu - ustawienia nie sa niezbedne
        pass


# ----------------------------------------------------------------- obrazy ----


def _register_heif():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:  # HEIC nie zadziala, reszta tak
        pass


def convert_image(src, dst, fmt, quality, max_side):
    """Zapisuje obraz jako fmt (nazwa Pillow). Zwraca uwage (tekst) albo None."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    _register_heif()
    try:
        im = Image.open(src)
    except UnidentifiedImageError:
        raise ValueError("nie mozna odczytac pliku jako obrazu (uszkodzony albo nieobslugiwany; "
                         "HEIC i AVIF wymagaja pakietu pillow-heif z install.bat)")
    with im:
        note = ("animacja - zapisano tylko pierwsza klatke (do animacji wybierz Wideo)"
                if getattr(im, "n_frames", 1) > 1 else None)
        im = ImageOps.exif_transpose(im)  # obrot z EXIF; reszta metadanych (GPS!) nie jest kopiowana
        if im.mode in ("I;16", "I;16L", "I;16B", "I"):  # 16 bitow: przeskalowac, convert() by obcial do bieli
            im = im.point(lambda v: v * (1 / 256)).convert("L")
        alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        if fmt in ("JPEG", "BMP"):  # bez przezroczystosci: bialy podklad
            if alpha:
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA" if alpha else "RGB")
        if fmt == "ICO":
            max_side = 256
        if max_side and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if fmt == "ICO":  # ikona jest kwadratowa: obraz na przezroczystym kwadracie
            s = max(im.size)
            canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            canvas.paste(im.convert("RGBA"), ((s - im.width) // 2, (s - im.height) // 2))
            canvas.save(dst, "ICO", sizes=[(n, n) for n in (16, 32, 48, 64, 128, 256) if n <= s] or [(s, s)])
        else:
            kw = {"JPEG": dict(quality=quality, optimize=True), "WEBP": dict(quality=quality, method=4),
                  "PNG": dict(optimize=True), "TIFF": dict(compression="tiff_lzw")}.get(fmt, {})
            im.save(dst, fmt, **kw)
    return note


# ------------------------------------------------------------------ wideo ----


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        raise RuntimeError("brak ffmpeg - uruchom install.bat (instaluje pakiet imageio-ffmpeg)")


def _info(path):
    """Opis pliku z ffmpeg (stderr polecenia -i): czas trwania, strumienie."""
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NOWIN)
    return r.stderr


def duration(path):
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", _info(path))
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0


def video_args(ext, quality, max_h):
    """Parametry wyjsciowe ffmpeg dla danego formatu (max_h = 0: bez zmiany rozmiaru)."""
    _, crf, q4, qa = QUALITY[quality]
    if ext == ".mp3":
        return ["-vn", "-c:a", "libmp3lame", "-q:a", str(qa)]
    if ext == ".gif":
        h = max_h or 480
        return ["-an", "-vf", "fps=12,scale=-2:'min(ih,%d)':flags=lanczos,split[a][b];"
                              "[a]palettegen[p];[b][p]paletteuse" % h]
    # parzyste wymiary sa wymagane przez H.264/yuv420p
    base = ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:'trunc(min(ih,%d)/2)*2'" % (max_h or 99999),
            "-pix_fmt", "yuv420p"]
    if ext in (".mp4", ".mkv"):
        return base + ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-c:a", "aac",
                       "-b:a", "160k"] + (["-movflags", "+faststart"] if ext == ".mp4" else [])
    if ext == ".webm":
        return base + ["-c:v", "libvpx-vp9", "-crf", str(crf + 8), "-b:v", "0", "-row-mt", "1",
                       "-deadline", "good", "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"]
    return base + ["-c:v", "mpeg4", "-q:v", str(q4), "-c:a", "libmp3lame", "-q:a", "3"]  # .avi


def convert_video(src, dst, ext, quality, max_h, progress=None, stop=None):
    """Konwersja ffmpeg. progress(0..1) w trakcie; zwraca False, gdy przerwano (stop)."""
    global _proc
    total = duration(src)
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", "-i", src] + video_args(ext, quality, max_h) + [dst]
    _proc = p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", creationflags=NOWIN)
    err = []
    for line in p.stdout:
        if stop is not None and stop.is_set():
            p.kill()
            break
        m = re.match(r"out_time_(?:us|ms)=(\d+)", line)  # oba pola sa w mikrosekundach
        if m:
            if total and progress:
                progress(min(1.0, int(m[1]) / 1e6 / total))
        elif not re.match(r"\w+=", line):  # reszta to komunikaty bledow
            err.append(line.strip())
    p.wait()
    _proc = None
    if (stop is not None and stop.is_set()) or p.returncode:
        if os.path.exists(dst):
            os.remove(dst)
        if p.returncode and not (stop is not None and stop.is_set()):
            raise RuntimeError(" ".join(err[-3:]) or "ffmpeg zakonczyl sie bledem %d" % p.returncode)
        return False
    return True


# ----------------------------------------------------------------- uruchom ----


def run(kind, fmt, inp, out_dir, quality="Dobra", size=0, log=print, progress=None, stop=None):
    """kind: 'obraz' | 'wideo'; fmt: klucz IMAGE_OUT / VIDEO_OUT; size: maks. dluzszy bok / wysokosc
    w px (0 = bez zmiany); progress(0..1) dla calosci; stop: threading.Event przerywajacy."""
    table = IMAGE_OUT if kind == "obraz" else VIDEO_OUT
    if fmt not in table or quality not in QUALITY:
        raise ValueError("nieznany format lub jakosc: %s / %s" % (fmt, quality))
    ext = table[fmt][0]
    todo = []
    for f in list_inputs(inp, IMG_IN if kind == "obraz" else VID_IN + (".gif",)):
        if matches(f, kind):
            todo.append(f)
        else:
            why = ("nieobslugiwany typ pliku" if not kind_of(f)
                   else "to %s, a wybrano konwersje: %s" % (kind_of(f), kind))
            log("Pominieto: %s (%s)" % (os.path.basename(f), why))
    if not todo:
        log("Brak plikow do przetworzenia.")
        return []
    os.makedirs(out_dir, exist_ok=True)
    res = []
    for i, f in enumerate(todo):
        if stop is not None and stop.is_set():
            log("Przerwano.")
            break
        name = os.path.basename(f)
        log("%s -> %s" % (name, fmt))
        dst = unique(os.path.join(out_dir, os.path.splitext(name)[0] + ext))
        try:
            if kind == "obraz":
                note = convert_image(f, dst, table[fmt][1], QUALITY[quality][0], size)
                if note:
                    log("  " + note)
            elif not convert_video(f, dst, ext, quality, size,
                                   (lambda fr, i=i: progress((i + fr) / len(todo))) if progress else None, stop):
                log("  Przerwano.")
                break
            log("  OK -> %s (%s -> %s)" % (dst, fsize(f), fsize(dst)))
            res.append(dst)
        except (ValueError, RuntimeError, OSError) as e:
            log("  BLAD: %s" % e)
        except Exception:
            log("  BLAD:\n" + traceback.format_exc())
        if progress:
            progress((i + 1) / len(todo))
    log("Zakonczono.")
    return res


# -------------------------------------------------------------------- GUI ----


def gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext

    try:  # przeciaganie plikow z Eksploratora; bez biblioteki program dziala, tylko bez tego
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
    except ImportError:
        root, DND_FILES = tk.Tk(), None
    root.title("Konwerter multimediow")
    cfg = load_settings()
    root.geometry(cfg.get("size", "900x740"))
    pad = dict(padx=6, pady=3)
    out_default = os.path.join(APP_DIR, "OUTPUT")
    in_dir = os.path.join(APP_DIR, "INPUT")

    v_kind = tk.StringVar(value=cfg.get("kind") if cfg.get("kind") in ("obraz", "wideo") else "obraz")
    v_fmt = tk.StringVar()
    v_q = tk.StringVar(value=cfg.get("q") if cfg.get("q") in QUALITY else "Dobra")
    v_size = tk.StringVar(value=cfg.get("size_px", "Oryginal"))
    v_out = tk.StringVar(value=cfg.get("out") or out_default)
    items = []  # pliki na liscie
    stop = threading.Event()

    # --- ustawienia
    top = ttk.LabelFrame(root, text="Co zrobic")
    top.pack(fill="x", **pad)
    ttk.Label(top, text="Konwertuj:").grid(row=0, column=0, sticky="w", **pad)
    kf = ttk.Frame(top)
    kf.grid(row=0, column=1, sticky="w")
    ttk.Radiobutton(kf, text="Obrazy", variable=v_kind, value="obraz").pack(side="left", padx=6)
    ttk.Radiobutton(kf, text="Wideo", variable=v_kind, value="wideo").pack(side="left", padx=6)
    ttk.Label(top, text="Na format:").grid(row=1, column=0, sticky="w", **pad)
    cb_fmt = ttk.Combobox(top, textvariable=v_fmt, state="readonly", width=52)
    cb_fmt.grid(row=1, column=1, sticky="w", **pad)
    ttk.Label(top, text="Jakosc:").grid(row=2, column=0, sticky="w", **pad)
    ttk.Combobox(top, textvariable=v_q, values=list(QUALITY), state="readonly",
                 width=20).grid(row=2, column=1, sticky="w", **pad)
    lbl_size = ttk.Label(top)
    lbl_size.grid(row=3, column=0, sticky="w", **pad)
    cb_size = ttk.Combobox(top, textvariable=v_size, state="readonly", width=12)
    cb_size.grid(row=3, column=1, sticky="w", **pad)

    def on_kind(*_):
        table = IMAGE_OUT if v_kind.get() == "obraz" else VIDEO_OUT
        cb_fmt["values"] = ["%s - %s" % (k, v[-1]) for k, v in table.items()]
        keys = list(table)
        cur = v_fmt.get().split(" - ")[0]
        cb_fmt.current(keys.index(cur) if cur in keys else 0)
        lbl_size.config(text="Maks. dluzszy bok (px):" if v_kind.get() == "obraz" else "Maks. wysokosc (px):")
        cb_size["values"] = SIZES[v_kind.get()]
        if v_size.get() not in SIZES[v_kind.get()]:
            v_size.set("Oryginal")

    v_fmt.set(cfg.get("fmt", ""))
    v_kind.trace_add("write", on_kind)
    on_kind()

    # --- lista plikow
    lf = ttk.LabelFrame(root, text="Pliki" + (" - przeciagnij tutaj pliki lub foldery" if DND_FILES else ""))
    lf.pack(fill="both", expand=True, **pad)
    lb = tk.Listbox(lf, selectmode="extended", height=8, activestyle="none")
    sb = ttk.Scrollbar(lf, command=lb.yview)
    lb.config(yscrollcommand=sb.set)
    lb.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
    sb.pack(side="left", fill="y", pady=6)
    side = ttk.Frame(lf)
    side.pack(side="left", fill="y", padx=6, pady=6)
    status = ttk.Label(root, foreground="#666")

    def refresh():
        lb.delete(0, "end")
        for i, p in enumerate(items, 1):
            lb.insert("end", "%2d.  %s   (%s, %s)" % (i, os.path.basename(p), kind_of(p), fsize(p)))
        nv = sum(kind_of(p) == "wideo" for p in items)
        status.config(text="Plikow: %d (obrazy: %d, wideo: %d)" % (len(items), len(items) - nv, nv) if items
                      else "Lista jest pusta - dodaj pliki przyciskiem lub przeciagnij je na liste.")

    def add(paths):
        skipped = []
        for p in map(os.path.normpath, list_inputs(list(paths), IMG_IN + VID_IN)):
            if not kind_of(p):
                skipped.append(os.path.basename(p))
            elif p not in items:
                items.append(p)
        if skipped:
            log("Pominieto (nieobslugiwany typ): " + ", ".join(skipped))
        if items and not any(matches(p, v_kind.get()) for p in items):
            v_kind.set(kind_of(items[0]))  # same wideo na liscie - przelacz na Wideo
        refresh()

    def remove(*_):
        for i in reversed(lb.curselection()):
            items.pop(i)
        refresh()

    def clear():
        items.clear()
        refresh()

    def open_item(e):
        i = lb.nearest(e.y)
        if items and hasattr(os, "startfile"):
            os.startfile(items[i])

    lb.bind("<Delete>", remove)
    lb.bind("<Double-Button-1>", open_item)
    if DND_FILES:
        for w in (lb, lf):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", lambda e: add(root.tk.splitlist(e.data)))
    types = [("Obrazy i wideo", " ".join("*" + e for e in IMG_IN + VID_IN))]
    for text, cmd in (("Dodaj pliki...", lambda: add(filedialog.askopenfilenames(filetypes=types, initialdir=in_dir))),
                      ("Dodaj folder...", lambda: add([d] if (d := filedialog.askdirectory(initialdir=in_dir)) else [])),
                      ("Usun z listy", remove), ("Wyczysc liste", clear)):
        ttk.Button(side, text=text, command=cmd, width=16).pack(pady=2)
    status.pack(anchor="w", padx=12)

    # --- wynik, przyciski, postep, log
    f = ttk.Frame(root)
    f.pack(fill="x", **pad)
    ttk.Label(f, text="Folder wyjsciowy:").grid(row=0, column=0, sticky="w", **pad)
    ttk.Entry(f, textvariable=v_out, width=70).grid(row=0, column=1, **pad)
    ttk.Button(f, text="Wybierz...", command=lambda: v_out.set(
        filedialog.askdirectory() or v_out.get())).grid(row=0, column=2, **pad)

    bar = ttk.Frame(root)
    bar.pack(pady=4)
    btn = ttk.Button(bar, text="Konwertuj")
    btn.pack(side="left", padx=6)
    btn_stop = ttk.Button(bar, text="Przerwij", state="disabled")
    btn_stop.pack(side="left", padx=6)

    def open_out():
        out = v_out.get().strip('" ')
        if out and hasattr(os, "startfile"):
            os.makedirs(out, exist_ok=True)
            os.startfile(out)

    ttk.Button(bar, text="Otworz folder wynikow", command=open_out).pack(side="left", padx=6)
    pbar = ttk.Progressbar(root, maximum=100)
    pbar.pack(fill="x", **pad)
    log_box = scrolledtext.ScrolledText(root, height=9)
    log_box.pack(fill="both", expand=True, **pad)

    def log(msg):
        def put():
            log_box.insert("end", str(msg) + "\n")
            log_box.see("end")
        root.after(0, put)

    def busy(on):
        btn.config(state="disabled" if on else "normal")
        btn_stop.config(state="normal" if on else "disabled")

    def start():
        out = v_out.get().strip('" ')
        if not items:
            return log("Dodaj pliki do listy (przycisk 'Dodaj pliki...' albo przeciagnij je na liste).")
        if not out:
            return log("Wskaz folder wyjsciowy.")
        kind, fmt = v_kind.get(), v_fmt.get().split(" - ")[0]
        size = 0 if v_size.get() == "Oryginal" else int(v_size.get())
        quality = v_q.get()  # zmienne Tk czytamy tu, w watku okna, nie w roboczym
        stop.clear()
        busy(True)
        log_box.delete("1.0", "end")
        pbar["value"] = 0

        def work():
            try:
                run(kind, fmt, list(items), out, quality, size, log,
                    lambda fr: root.after(0, lambda: pbar.config(value=fr * 100)), stop)
            finally:
                root.after(0, busy, False)

        threading.Thread(target=work, daemon=True).start()

    btn.config(command=start)
    btn_stop.config(command=lambda: (stop.set(), log("Przerywanie...")))

    def on_close():
        out = v_out.get().strip('" ')
        save_settings({"kind": v_kind.get(), "fmt": v_fmt.get().split(" - ")[0], "q": v_q.get(),
                       "size_px": v_size.get(),
                       # domyslny OUTPUT nie zapisany - po przeniesieniu programu ma wskazywac nowe miejsce
                       "out": "" if os.path.normpath(out) == os.path.normpath(out_default) else out,
                       # zminimalizowane/zmaksymalizowane okno - zostaje poprzedni rozmiar
                       "size": "%dx%d" % (root.winfo_width(), root.winfo_height())
                       if root.state() == "normal" else cfg.get("size", "900x740")})
        stop.set()
        if _proc is not None and _proc.poll() is None:
            _proc.kill()  # watek usunie niedokonczony plik
        root.after(300, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    add([in_dir] if os.path.isdir(in_dir) else [])  # pliki wrzucone do INPUT
    if "--selftest" in sys.argv:
        root.after(200, root.destroy)
    import aktualizacja
    aktualizacja.start(root, "DawidBochno/Konwerter-multimediow", "main", "konwerter.py")
    root.mainloop()


# --------------------------------------------------------------- selftest ----


def selftest():
    import tempfile
    from PIL import Image

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    quiet = lambda m: None  # noqa: E731

    # --- obrazy
    png = os.path.join(tmp, "zdjecie ąę.png")
    Image.new("RGBA", (400, 200), (255, 0, 0, 0)).save(png)  # w pelni przezroczysty
    kinds = {"JPG": "JPEG", "PNG": "PNG", "WEBP": "WEBP", "BMP": "BMP", "TIFF": "TIFF", "GIF": "GIF", "ICO": "ICO"}
    for fmt, pil in kinds.items():
        r = run("obraz", fmt, [png], out, log=quiet)
        assert len(r) == 1 and r[0].endswith(IMAGE_OUT[fmt][0]), (fmt, r)
        with Image.open(r[0]) as im:
            assert im.format == pil, (fmt, im.format)
            if fmt == "JPG":
                assert im.mode == "RGB" and min(im.getpixel((5, 5))) >= 250, im.getpixel((5, 5))  # bialy podklad
    r = run("obraz", "WEBP", [png], out, "Mala (maly plik)", 100, log=quiet)
    with Image.open(r[0]) as im:
        assert im.size == (100, 50), im.size
    assert r[0].endswith("_2.webp") or "_" in os.path.basename(r[0])  # istniejacy plik nie jest nadpisany
    assert len(os.listdir(out)) == 8
    jpg = os.path.join(tmp, "obrocone.jpg")
    ex = Image.Exif()
    ex[274] = 6  # orientacja EXIF: obrot o 90 stopni
    Image.new("RGB", (200, 100), (10, 200, 10)).save(jpg, exif=ex)
    r = run("obraz", "PNG", [jpg], out, log=quiet)
    with Image.open(r[0]) as im:
        assert im.size == (100, 200) and not im.getexif(), im.size  # obrocony i bez EXIF
    t16 = os.path.join(tmp, "skan16.tif")
    Image.new("I;16", (8, 8), 40000).save(t16)  # 16 bitow: nie moze wyjsc bialy (40000 -> 156)
    for fmt in ("JPG", "PNG"):
        with Image.open(run("obraz", fmt, [t16], out, log=quiet)[0]) as im:
            assert im.convert("L").getpixel((1, 1)) in range(154, 159), (fmt, im.convert("L").getpixel((1, 1)))
    gif = os.path.join(tmp, "anim.gif")
    a, b = Image.new("RGB", (64, 64), "red"), Image.new("RGB", (64, 64), "blue")
    a.save(gif, save_all=True, append_images=[b], duration=200, loop=0)
    msgs = []
    assert len(run("obraz", "PNG", [gif], out, log=msgs.append)) == 1 and "pierwsza klatke" in "".join(msgs)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        Image.new("RGB", (64, 48), "blue").save(os.path.join(tmp, "tel.heic"), format="HEIF")
        r = run("obraz", "JPG", [os.path.join(tmp, "tel.heic")], out, log=quiet)
        with Image.open(r[0]) as im:
            assert im.size == (64, 48)
    except ImportError:
        assert not os.environ.get("CI"), "w CI wymagany pillow-heif"
    bad = os.path.join(tmp, "zepsuty.png")
    open(bad, "wb").write(b"to nie jest obraz")
    msgs = []
    assert run("obraz", "JPG", [bad], out, log=msgs.append) == [] and "BLAD: nie mozna odczytac" in "".join(msgs)

    # --- foldery i pomijanie plikow nie tego rodzaju
    mix = os.path.join(tmp, "mix")
    os.makedirs(mix)
    for n in ("b.jpg", "a.png", "c.mp4", "notatka.txt"):
        open(os.path.join(mix, n), "wb").write(b"x")
    assert [os.path.basename(p) for p in list_inputs(mix, IMG_IN)] == ["a.png", "b.jpg"]
    assert [os.path.basename(p) for p in list_inputs([mix, png], VID_IN)] == ["c.mp4", "zdjecie ąę.png"]
    msgs = []
    assert run("wideo", "MP4", [png, os.path.join(mix, "notatka.txt")], out, log=msgs.append) == []
    text = "\n".join(msgs)
    assert "to obraz, a wybrano konwersje: wideo" in text and "nieobslugiwany typ pliku" in text, text
    try:
        run("obraz", "XYZ", [png], out, log=quiet)
        raise AssertionError("powinien byc blad")
    except ValueError:
        pass

    # --- wideo (prawdziwy ffmpeg)
    mp4 = os.path.join(tmp, "film ąę.mp4")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", mp4], check=True, creationflags=NOWIN)
    assert 0.5 < duration(mp4) < 2
    expect = {"MP4": ("Video: h264", "Audio: aac"), "WEBM": ("Video: vp9", "Audio: opus"),
              "MKV": ("Video: h264", "Audio: aac"), "AVI": ("Video: mpeg4", "Audio: mp3"),
              "GIF": ("Video: gif",), "MP3": ("Audio: mp3",)}
    calls = []
    for fmt, need in expect.items():
        r = run("wideo", fmt, [mp4], out, progress=calls.append, log=quiet)
        assert len(r) == 1 and os.path.getsize(r[0]) > 0, fmt
        info = _info(r[0])
        assert all(n in info for n in need), (fmt, info)
        assert fmt != "MP3" or "Video:" not in info
    assert calls and max(calls) == 1.0 and all(0 <= c <= 1 for c in calls), calls
    r = run("wideo", "MP4", [mp4], out, size=120, log=quiet)
    assert "160x120" in _info(r[0]), _info(r[0])
    r = run("wideo", "MP4", [gif], out, log=quiet)  # animowany GIF -> MP4
    assert len(r) == 1 and "Video: h264" in _info(r[0])
    broken = os.path.join(tmp, "zepsuty.mkv")
    open(broken, "wb").write(b"to nie jest film")
    msgs = []
    assert run("wideo", "MP4", [broken], out, log=msgs.append) == [] and "BLAD:" in "".join(msgs)
    assert not [f for f in os.listdir(out) if f.startswith("zepsuty")]  # bez niedokonczonych plikow
    ev = threading.Event()
    ev.set()
    msgs = []
    assert run("wideo", "MP4", [mp4], out, log=msgs.append, stop=ev) == [] and "Przerwano." in msgs

    # --- ustawienia
    cfg = os.path.join(tmp, "ustawienia.json")
    assert load_settings(cfg) == {}
    save_settings({"kind": "wideo", "out": "C:\\Wyniki ąę"}, cfg)
    assert load_settings(cfg) == {"kind": "wideo", "out": "C:\\Wyniki ąę"}
    open(cfg, "w").write("{zepsuty")
    assert load_settings(cfg) == {}
    import aktualizacja
    aktualizacja.selftest()
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        if "--gui" in sys.argv:
            gui()
    else:
        gui()
