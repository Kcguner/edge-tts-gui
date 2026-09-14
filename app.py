# --- START OF FILE final.py ---
from __future__ import annotations

# --- Imports ---
import tkinter as tk
import customtkinter as ctk
import asyncio
import edge_tts
import edge_tts.exceptions
import tempfile
import os
import re
import threading
import time
import json
import sys
from tkinter import filedialog

# Konsol cp1252/cp1254 (Turkce Windows) iken emoji/Turkce karakterli
# print'ler UnicodeEncodeError verip Tk callback'lerini patlatmasin.
for _stream_name in ("stdout", "stderr"):
    try:
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
del _stream_name
try:
    del _stream
except NameError:
    pass
# from mutagen.mp3 import MP3 # Option: Remove if no duration fallback planned
# from mutagen import MutagenError # Option: Remove if no duration fallback planned
try:
    from just_playback import Playback
    JUST_PLAYBACK_AVAILABLE = True
except ImportError:
    print("ERROR: Required library 'just_playback' not found. Please install it: pip install just_playback")
    JUST_PLAYBACK_AVAILABLE = False
except Exception as e:
    # Catch other potential errors during just_playback import/initialization
    print(f"ERROR: Failed to import or initialize just_playback: {e}")
    JUST_PLAYBACK_AVAILABLE = False


# --- Configuration ---
# Set initial mode to follow the system setting
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue") # Options: "blue", "green", "dark-blue"

# --- Constants ---
AUDIO_UPDATE_INTERVAL_MS = 100 # Progress bar update interval (ms)
MIN_WINDOW_WIDTH = 700
MIN_WINDOW_HEIGHT = 650
SEEK_INTERVAL_SECONDS = 5 # Number of seconds to jump forward/backward
# DEFAULT_APPEARANCE_MODE = "Light" # REMOVED - Now starts with "System"
TEXTBOX_PLACEHOLDER_TEXT = "Enter text here or load from a file..."
# Acilista ornek olarak gelen, sesleri hemen denemeye yarayan gercek metin.
# Kullanici ilk kez yazi yazdiginda/yapistirdiginda/dosya yuklediginde
# otomatik temizlenir (asagidaki _clear_default_sample).
DEFAULT_SAMPLE_TEXT = (
    "Hello and welcome to the Edge text to speech demo. "
    "This short sample helps you try different voices and languages. "
    "Simply replace this text with your own words whenever you are ready."
)
# Choose a placeholder color that works reasonably well in both light/dark modes
TEXTBOX_PLACEHOLDER_COLOR = "#888888" # Medium-Gray
# Favorites & settings persistence - handles both script and PyInstaller exe.
# Portable exe dizinine yazmak (Program Files / yetki hatasi) yerine
# kullanici profilini kullan; eski konumdaki dosyayi tasi.
APP_NAME = "EdgeTTS-GUI"

def _resolve_app_data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg, APP_NAME)


def _legacy_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _migrate_legacy_file(filename: str, target: str):
    legacy = os.path.join(_legacy_base_dir(), filename)
    if target != legacy and os.path.exists(legacy) and not os.path.exists(target):
        try:
            with open(legacy, 'r', encoding='utf-8') as src, open(target, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
            print(f"INFO: Migrated legacy {filename} to {target}")
        except OSError as e:
            print(f"WARN: Could not migrate {filename}: {e}")


def _resolve_data_file(filename: str) -> str:
    app_dir = _resolve_app_data_dir()
    target = os.path.join(app_dir, filename)
    try:
        os.makedirs(app_dir, exist_ok=True)
        _migrate_legacy_file(filename, target)
    except OSError:
        target = os.path.join(_legacy_base_dir(), filename)
    return target


def _resolve_favorites_file() -> str:
    return _resolve_data_file("favorites.json")


def _atomic_write_json(path: str, data) -> bool:
    """Atomik JSON yazma: .tmp + os.replace ile yarim dosya birakmaz."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"WARN: Could not write {path}: {e}")
        return False


def _read_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        print(f"WARN: Could not read {path}: {e}")
    return default


def _norm_search(s: str) -> str:
    """Aksan/duyarliliksiz arama: 'turk' -> 'türkçe' eslesir."""
    try:
        import unicodedata
        t = (s or "").lower().strip()
        t = unicodedata.normalize("NFKD", t)
        t = "".join(c for c in t if not unicodedata.combining(c))
        # Turkce'ye ozel (NFKD sonrasi da kalanlar icin)
        for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
            t = t.replace(a, b)
        return t
    except Exception:
        return (s or "").lower().strip()


FAVORITES_FILE = _resolve_favorites_file()
SETTINGS_FILE = _resolve_data_file("settings.json")
PROFILES_FILE = _resolve_data_file("profiles.json")
MAX_VOICE_PROFILES = 4


class ScrollableDropdown(ctk.CTkFrame):
    """CTkComboBox yerine: tk.Menu kullanmaz, Listbox+scrollbar+wheel ile 100+ ogede sorunsuz kayar.

    CTkComboBox'in alt menu olarak kullandigi tkinter.Menu, Windows'ta cok sayida
    ogede ekran disina tasip mouse-wheel ile kaymaz. Bu widget bunun yerine
    CTkToplevel icinde Listbox + scrollbar acar; wheel her platformda calisir.
    Alt kumede CTkComboBox ile uyumlu API sunar: get/set/configure(values,state,command).

    Odak kurali: popup disari tiklamayla kapanirsa tiklanan yerin odagi calinmaz
    (yoksa metin kutusuna tiklayip yazmak imkansizlasir). Odak yalnizca popup
    icindeyken (secim/Escape ile) dugmeye geri verilir.
    """

    POPUP_MAX_HEIGHT = 300

    def __init__(self, master, values=None, command=None, width: int = 180,
                 height: int = 28, state: str = "normal",
                 search_placeholder: str = "Dil ara...", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._values: list[str] = list(values) if values else []
        self._command = command
        self._state: str = state
        self._current: str = self._values[0] if self._values else ""
        self._search_placeholder = search_placeholder
        self._popup = None
        self._popup_listbox = None
        self._popup_search = None
        # Ana pencereye bagli ButtonPress dinleyicisinin funcid'si.
        # bind_all KULLANMIYORUZ: tum uygulamaya yayilir ve unbind_all
        # CTk'nin dahili/gelecekteki global baglantilarini da siler.
        # toplevel.bind ise yalnizca ana pencere widget'larinda calisir
        # (popup ayri bir toplevel oldugu icin onun tiklamalari buraya dusmez).
        self._outside_press_binding = None

        self.grid_columnconfigure(0, weight=1)
        self.display_btn = ctk.CTkButton(
            self, text=self._display_text(), anchor="w",
            height=height, width=width, command=self.toggle_popup,
            state=state,
        )
        self.display_btn.grid(row=0, column=0, sticky="ew")

    # -- public API (CTkComboBox subset) --
    def _display_text(self) -> str:
        txt = self._current or "—"
        # Buton genisliginde tasmamasi icin kirp
        return (txt[:32] + "…") if len(txt) > 33 else (txt + "  ▼")

    def get(self) -> str:
        return self._current

    def set(self, value: str):
        self._current = value
        if hasattr(self, 'display_btn') and self.display_btn.winfo_exists():
            try:
                self.display_btn.configure(text=self._display_text())
            except Exception:
                pass

    def configure(self, **kwargs):
        if "values" in kwargs:
            self._values = list(kwargs.pop("values") or [])
            if self._current not in self._values and self._values:
                # Mevcut secim listede yoksa sessizce ilk ogeye gecme;
                # cagiran taraf set() ile karar verir. Bos ise ilk degeri al.
                if not self._current:
                    self.set(self._values[0])
                else:
                    try:
                        self.display_btn.configure(text=self._display_text())
                    except Exception:
                        pass
            if self._popup_listbox is not None and self._popup is not None:
                try:
                    self._refresh_popup_list("")
                except Exception:
                    pass
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            try:
                self.display_btn.configure(state=self._state)
            except Exception:
                pass
        if "width" in kwargs:
            try:
                self.display_btn.configure(width=kwargs.pop("width"))
            except Exception:
                pass
        if kwargs:
            try:
                super().configure(**kwargs)
            except Exception:
                pass

    def cget(self, attribute_name: str):
        if attribute_name == "values":
            return list(self._values)
        if attribute_name == "state":
            return self._state
        if attribute_name == "command":
            return self._command
        return super().cget(attribute_name)

    # -- popup --
    def toggle_popup(self):
        if self._state == "disabled":
            return
        if self._popup is not None and self._popup.winfo_exists():
            self.close_popup()
        else:
            self.open_popup()

    def open_popup(self):
        if self._state == "disabled":
            return
        try:
            self.close_popup(restore_focus=False)
        except Exception:
            pass
        try:
            toplevel = self.winfo_toplevel()
            popup = ctk.CTkToplevel(toplevel)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.attributes("-topmost", True)
            try:
                popup.transient(toplevel)
            except Exception:
                pass
            self._popup = popup

            search = ctk.CTkEntry(popup, placeholder_text=self._search_placeholder, height=28)
            search.pack(fill="x", padx=6, pady=(6, 4))
            search.bind("<KeyRelease>", lambda e: self._refresh_popup_list(search.get()))
            search.bind("<Escape>", lambda e: self.close_popup())
            # Odak arama kutusundayken Enter'a basmak vurgulu/ilki secmeli;
            # yoksa kullanici yazip Enter'a basinca hicbir sey olmaz.
            search.bind("<Return>", lambda e: self._choose_highlighted())
            search.bind("<KP_Enter>", lambda e: self._choose_highlighted())
            search.bind("<Down>", lambda e: self._focus_listbox())
            self._popup_search = search

            list_frame = ctk.CTkFrame(popup, fg_color="transparent")
            list_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            list_frame.grid_columnconfigure(0, weight=1)
            list_frame.grid_rowconfigure(0, weight=1)

            lb = tk.Listbox(list_frame, height=10, exportselection=False,
                            activestyle="none", selectmode=tk.SINGLE)
            lb.grid(row=0, column=0, sticky="nsew")
            sb = ctk.CTkScrollbar(list_frame, command=lb.yview)
            sb.grid(row=0, column=1, sticky="ns")
            lb.configure(yscrollcommand=sb.set)
            self._apply_listbox_theme(lb)
            lb.bind("<<ListboxSelect>>", self._on_popup_select)
            # Cift tiklama: secimi onayla (sadece kapat degil). Tek tiklama
            # zaten <<ListboxSelect>> ile secer; cift tiklamanin ikinci
            # basi ayni satirdaysa secim degismezdi ve popup secimsiz
            # kapanirdi - bunun yerine vurgulu ogeyi onayla.
            lb.bind("<Double-Button-1>", lambda e: self._choose_highlighted())
            lb.bind("<Return>", lambda e: self._choose_highlighted())
            lb.bind("<KP_Enter>", lambda e: self._choose_highlighted())
            lb.bind("<Escape>", lambda e: self.close_popup())
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                lb.bind(seq, self._on_popup_wheel)
                try:
                    sb.bind(seq, self._on_popup_wheel)
                except Exception:
                    pass
            self._popup_listbox = lb

            self._refresh_popup_list("")
            # Konum: butonun hemen alti, ekran disina tasmayacak sekilde
            try:
                self.update_idletasks()
                popup.update_idletasks()
                x = self.display_btn.winfo_rootx()
                y = self.display_btn.winfo_rooty() + self.display_btn.winfo_height() + 4
                w = max(self.display_btn.winfo_width(), 200)
                n = lb.size()
                h = min(self.POPUP_MAX_HEIGHT, max(120, 34 + n * 22))
                sw = popup.winfo_screenwidth()
                sh = popup.winfo_screenheight()
                if x + w > sw - 8:
                    x = max(8, sw - w - 8)
                if y + h > sh - 40:
                    y = max(8, self.display_btn.winfo_rooty() - h - 4)
                popup.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                pass
            popup.deiconify()
            try:
                search.focus_set()
            except Exception:
                pass
            # Disari tiklayinca kapat. Ana pencereye bagli dinleyici yalnizca
            # ana pencere widget'larinda tetiklenir (popup ayri toplevel),
            # bu yuzden ic/dis ayirt etmeye gerek yoktur; tek istisna
            # secim dugmesinin kendisidir (ac/kapa isini toggle_popup yapar).
            try:
                top = toplevel
                if self._outside_press_binding is not None:
                    try:
                        top.unbind("<ButtonPress-1>", self._outside_press_binding)
                    except Exception:
                        pass
                    self._outside_press_binding = None
                self._outside_press_binding = top.bind(
                    "<ButtonPress-1>", self._on_outside_press, add="+")
            except Exception:
                self._outside_press_binding = None
            try:
                popup.protocol("WM_DELETE_WINDOW", self.close_popup)
            except Exception:
                pass
        except Exception as e:
            print(f"WARN: Could not open language dropdown: {e}")
            self._popup = None

    def _focus_listbox(self):
        try:
            if self._popup_listbox is not None:
                self._popup_listbox.focus_set()
                if not self._popup_listbox.curselection() and self._popup_listbox.size() > 0:
                    self._popup_listbox.selection_set(0)
        except Exception:
            pass

    def _choose_highlighted(self):
        try:
            lb = self._popup_listbox
            if lb is None or not lb.winfo_exists():
                return
            try:
                if str(lb.cget("state")) == "disabled":
                    return
            except Exception:
                pass
            sel = lb.curselection()
            if sel:
                self._on_popup_select()
            elif lb.size() > 0:
                first = lb.get(0)
                if first == "Sonuç yok":
                    return
                lb.selection_set(0)
                self._on_popup_select()
        except Exception:
            pass

    def _on_outside_press(self, event=None):
        """Ana pencerede popup disina tiklaninca kapatir.

        Tiklanan widget'in odagi calinmaz: tiklama zaten ilgili widget'a
        (orn. metin kutusu) odagi vermistir, kapatma bunu geri almamalidir.
        """
        try:
            w = event.widget if event is not None else None
            if w is not None:
                try:
                    btn_path = str(self.display_btn)
                    # Secim dugmesi (ve ic cocuklari) toggle_popup'a aittir.
                    if str(w) == btn_path or str(w).startswith(btn_path + "."):
                        return
                except Exception:
                    pass
            self.close_popup(restore_focus=False)
        except Exception:
            pass

    def _on_popup_wheel(self, event=None):
        try:
            lb = self._popup_listbox
            if lb is None:
                return "break"
            if event is None:
                return "break"
            if getattr(event, "num", None) == 4:
                lb.yview_scroll(-2, "units")
            elif getattr(event, "num", None) == 5:
                lb.yview_scroll(2, "units")
            else:
                delta = getattr(event, "delta", 0)
                steps = int(-1 * (delta / 120)) if delta else 0
                if steps == 0 and delta != 0:
                    steps = -1 if delta > 0 else 1
                lb.yview_scroll(steps, "units")
        except Exception:
            pass
        return "break"

    def _filtered_values(self, term: str) -> list[str]:
        t = _norm_search(term)
        if not t:
            return list(self._values)
        return [v for v in self._values if t in _norm_search(v)]

    def _refresh_popup_list(self, term: str):
        lb = self._popup_listbox
        if lb is None or not lb.winfo_exists():
            return
        items = self._filtered_values(term)
        lb.delete(0, tk.END)
        for v in items:
            lb.insert(tk.END, v)
        if not items:
            lb.insert(tk.END, "Sonuç yok")
            lb.configure(state="disabled")
        else:
            try:
                lb.configure(state="normal")
            except Exception:
                pass
            # Mevcut secimi vurgula
            try:
                if self._current in items:
                    idx = items.index(self._current)
                    lb.selection_set(idx)
                    lb.activate(idx)
                    lb.see(idx)
                else:
                    lb.selection_set(0)
                    lb.see(0)
            except Exception:
                pass

    def _on_popup_select(self, event=None):
        try:
            lb = self._popup_listbox
            if lb is None or str(lb.cget("state")) == "disabled":
                return
            sel = lb.curselection()
            if not sel:
                return
            value = lb.get(sel[0])
            if value == "Sonuç yok":
                return
            self.set(value)
            cb = self._command
            # Secim sonrasi kapat (command popup yokken calissin)
            self.close_popup()
            if cb is not None:
                try:
                    cb(value)
                except Exception as e:
                    print(f"WARN: Dropdown command failed: {e}")
        except Exception:
            pass

    def close_popup(self, restore_focus: bool = True):
        """Popup'i kapatir.

        restore_focus=True iken odak, yalnizca popup icindeyken dugmeye
        geri verilir. Popup disina tiklanarak kapatmada (restore_focus=False)
        tiklanan widget'in odagi kesinlikle calinmaz; aksi halde metin
        kutusuna tiklayip yazmak mumkun olmaz (uygulama "kitlenmis" gibi
        gorunur).
        """
        popup = self._popup
        try:
            if self._outside_press_binding is not None:
                try:
                    self.winfo_toplevel().unbind(
                        "<ButtonPress-1>", self._outside_press_binding)
                except Exception:
                    pass
                self._outside_press_binding = None
        except Exception:
            pass
        self._popup_listbox = None
        self._popup_search = None
        try:
            if popup is not None and popup.winfo_exists():
                popup.destroy()
        except Exception:
            pass
        self._popup = None
        if restore_focus:
            take_focus = False
            try:
                try:
                    focused = self.winfo_toplevel().focus_get()
                except Exception:
                    focused = None
                if focused is None:
                    # Odak bosta kaldi (popup ile yok oldu) -> dugmeye ver
                    take_focus = True
                elif popup is not None:
                    try:
                        take_focus = str(focused).startswith(str(popup))
                    except Exception:
                        take_focus = False
            except Exception:
                take_focus = False
            if take_focus:
                try:
                    self.display_btn.focus_set()
                except Exception:
                    pass

    def _apply_listbox_theme(self, lb=None):
        try:
            lb = lb or self._popup_listbox
            if lb is None:
                return
            mode = ctk.get_appearance_mode()
            if mode == "Dark":
                lb.configure(bg="#2b2b2b", fg="#dce4ee", selectbackground="#1f6aa5",
                             selectforeground="white", highlightbackground="#2b2b2b",
                             highlightcolor="#2b2b2b")
            else:
                lb.configure(bg="white", fg="black", selectbackground="#1f6aa5",
                             selectforeground="white", highlightbackground="white",
                             highlightcolor="white")
        except Exception:
            pass

    def refresh_theme(self):
        self._apply_listbox_theme()

# --- Main Application ---
class EdgeTTSApp(ctk.CTk):
    """
    GUI application to generate Text-to-Speech using Microsoft Edge TTS
    and play it back using the just_playback library.
    Follows system theme initially, with a toggle override.
    Includes Textbox placeholder simulation.
    """
    def __init__(self):
        """Initializes the main window, UI elements, and application state."""
        # NOTE: Appearance mode ("System") is set *before* initializing CTk object
        super().__init__()

        # --- Theme is now initially System ---
        print(f"INFO: Initial appearance mode requested: 'System'")

        self.title("Edge TTS Text-to-Speech")
        self.resizable(True, True)
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)

        # Initialize Audio Player
        self.player: Playback | None = None
        self.just_playback_initialized: bool = False
        if JUST_PLAYBACK_AVAILABLE:
            try:
                self.player = Playback()
                self.just_playback_initialized = True
                print("INFO: just_playback initialized successfully.")
            except Exception as e:
                print(f"ERROR: Failed to initialize just_playback: {e}")
                self.just_playback_initialized = False

        # Application State
        self.voices_dict: dict[str, str] = {} # {Display Name: ShortName}
        self._all_voice_display_names: list[str] = []
        self.voices_raw: list[dict] = []
        self.voice_display_to_raw: dict[str, dict] = {}
        self.locale_to_lang_display: dict[str, str] = {}
        self.lang_display_to_locale: dict[str, str] = {}
        self.favorite_voices: set[str] = set()  # Set of ShortName
        self.load_favorites()
        # Ses profilleri: {"1": {"name":..,"voice":short,"voice_label":..,"rate":..,"pitch":..}, ...}
        self.voice_profiles: dict[str, dict] = {}
        self.load_voice_profiles()
        self.app_settings: dict = _read_json(SETTINGS_FILE, {}) if 'SETTINGS_FILE' in globals() else {}
        self.current_volume: float = float(self.app_settings.get("volume", 1.0)) if isinstance(self.app_settings.get("volume", 1.0), (int, float)) else 1.0
        self.current_volume = max(0.0, min(1.0, self.current_volume))
        # Kaydedilmis tema varsa acilista uygula (System varsayilan)
        try:
            _saved_theme = self.app_settings.get("theme")
            if _saved_theme in ("Light", "Dark"):
                ctk.set_appearance_mode(_saved_theme)
                print(f"INFO: Restored appearance mode from settings: '{_saved_theme}'")
        except Exception:
            pass
        self.audio_file_path: str | None = None # Path to the temporary audio file
        self.audio_duration: float = 0.0 # Audio duration in seconds
        # Uretim suruyor mu? True iken metin kutusu/kontroller ACIK kalir
        # (uretim baslarken metin/ses/hiz snapshot alinir), yalnizca
        # Generate dugmesi kilitli kalir. Tum bitis yollari False yapar.
        self._generating: bool = False
        self._after_id_update_progress: str | None = None # ID for the 'after' job updating progress
        self._slider_being_dragged: bool = False # Flag if user is dragging the progress slider
        self._settings_after_id: str | None = None
        self._search_after_id: str | None = None
        self._pending_voice_shortname: str | None = self.app_settings.get("selected_voice")

        # Placeholder state
        self.textbox_placeholder_active = False
        # Acilis ornek metni ekrandayken True; ilk gercek girdiyle temizlenir
        self.textbox_default_sample_active = False
        self.default_textbox_color = None # Will be fetched after widget creation

        self._build_ui() # Build the UI
        self._apply_initial_settings_to_widgets()

        # Initial Actions
        self.after(10, self._fetch_default_textbox_color) # Schedule fetching color early
        self.after(20, self._set_initial_textbox_placeholder) # Set initial placeholder state after color fetch attempt

        # Slider etiketleri _apply_initial_settings_to_widgets icinde ayarlandi
        if self.just_playback_initialized:
             self.update_status("Loading voices..."); self.load_voices_async()
        else:
             self.update_status("❌ Error: Audio library init failed. Audio disabled.");
             self.set_ui_state('error_no_audio')

        # Set initial state of the theme switch based on the *actual* mode determined by "System"
        # Needs a slight delay for the system mode to be resolved and applied
        self.after(50, self._update_theme_switch_state)
        print(f"INFO: Actual initial mode (after System resolution): '{ctk.get_appearance_mode()}'")


    def _build_ui(self):
        """Creates all user interface elements (widgets)."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1) # Textbox row expands
        self.grid_rowconfigure(3, weight=0) # Controls row fixed
        self.grid_rowconfigure(5, weight=0) # Helper row fixed
        self.grid_rowconfigure(6, weight=0) # Player row fixed

        # --- Input Area ---
        input_header_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_header_frame.grid(row=0, column=0, padx=20, pady=(10, 0), sticky="ew")
        input_header_frame.grid_columnconfigure(0, weight=1) # Label expands
        ctk.CTkLabel(input_header_frame, text="Input Text", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")

        # Theme Toggle Switch
        self.theme_switch = ctk.CTkSwitch(
            input_header_frame,
            text="Dark Mode",
            command=self._toggle_theme_override, # Changed command name
            onvalue=1,  # Represents "Dark" mode ON
            offvalue=0  # Represents "Dark" mode OFF (i.e., Light mode)
        )
        self.theme_switch.grid(row=0, column=1, padx=(10, 5), sticky="e")

        self.clear_btn = ctk.CTkButton(input_header_frame, text="🗑 Clear", width=70, height=28, fg_color="gray40", hover_color="gray30", command=self.clear_text)
        self.clear_btn.grid(row=0, column=2, padx=(0, 5), sticky="e")

        self.paste_btn = ctk.CTkButton(input_header_frame, text="📋 Paste", width=70, height=28, fg_color="gray40", hover_color="gray30", command=self.paste_from_clipboard)
        self.paste_btn.grid(row=0, column=3, padx=(0, 5), sticky="e")

        self.load_file_btn = ctk.CTkButton(input_header_frame, text="Load File...", width=100, command=self.load_text_from_file)
        self.load_file_btn.grid(row=0, column=4, padx=(0, 5), sticky="e")


        input_frame = ctk.CTkFrame(self)
        input_frame.grid(row=1, column=0, padx=20, pady=5, sticky="nsew")
        input_frame.grid_rowconfigure(0, weight=1); input_frame.grid_rowconfigure(1, weight=0); input_frame.grid_columnconfigure(0, weight=1)

        # --- Textbox Setup ---
        self.textbox = ctk.CTkTextbox(input_frame, wrap="word")
        self.textbox.grid(row=0, column=0, padx=5, pady=(5, 2), sticky="nsew")

        # Char counter + word count (kritik iyileştirme)
        self.char_counter_label = ctk.CTkLabel(input_frame, text="0 / 10.000 karakter  •  0 kelime", font=ctk.CTkFont(size=11), text_color="gray60", anchor="e")
        self.char_counter_label.grid(row=1, column=0, padx=5, pady=(0, 5), sticky="e")

        # Bind focus events for placeholder simulation
        self.textbox.bind("<FocusIn>", self._on_textbox_focus_in)
        self.textbox.bind("<FocusOut>", self._on_textbox_focus_out)

        # Detect text changes
        self.textbox.bind("<KeyRelease>", self._on_textbox_change)
        # Acilis ornek metni: ilk gercek karakter girisinde temizlenir
        # (odaklanma/gezinme tuslari ornegi korur)
        self.textbox.bind("<KeyPress>", self._on_textbox_sample_key)
        # Klavye ile yapistirma dugme yolundan gecmez; ornege eklenmesin
        self.textbox.bind("<<Paste>>", self._on_textbox_paste_sample, add="+")

        # --- Controls Area (Voice & Adjustments) ---
        # [Rest of the controls setup remains the same as before]
        ctk.CTkLabel(self, text="Voice & Adjustments", font=ctk.CTkFont(size=14, weight="bold")).grid(row=2, column=0, padx=20, pady=(10, 0), sticky="w")
        controls_frame = ctk.CTkFrame(self)
        controls_frame.grid(row=3, column=0, padx=20, pady=5, sticky="ew")
        controls_frame.grid_columnconfigure(0, weight=1, uniform="group1"); controls_frame.grid_columnconfigure(1, weight=2, uniform="group1")
        voice_select_frame = ctk.CTkFrame(controls_frame); voice_select_frame.grid(row=0, column=0, padx=(0, 5), pady=5, sticky="nsew")
        voice_select_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(voice_select_frame, text="Select Voice", font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=5, pady=(5, 2), sticky="w")
        # Filter frame: Language + Gender (kritik iyileştirme)
        # NOT: Dil filtresi icin CTkComboBox KULLANMIYORUZ. CTkComboBox altta
        # tkinter.Menu acar; 100+ dilde Windows'ta ekran disina tasip wheel
        # ile kaymaz. ScrollableDropdown Listbox+scrollbar+wheel kullanir.
        filter_frame = ctk.CTkFrame(voice_select_frame, fg_color="transparent")
        filter_frame.grid(row=1, column=0, padx=5, pady=(0, 2), sticky="ew")
        filter_frame.grid_columnconfigure(0, weight=2)
        filter_frame.grid_columnconfigure(1, weight=1)
        self.lang_filter_combo = ScrollableDropdown(filter_frame, values=["All Languages"], state="disabled", command=self._on_filter_change, width=180)
        self.lang_filter_combo.set("All Languages")
        self.lang_filter_combo.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.gender_filter_combo = ctk.CTkComboBox(filter_frame, values=["All", "Male", "Female"], state="disabled", command=self._on_filter_change, width=80)
        self.gender_filter_combo.set("All")
        self.gender_filter_combo.grid(row=0, column=1, sticky="ew")
        self.voice_search_entry = ctk.CTkEntry(voice_select_frame, placeholder_text="Search voice..."); self.voice_search_entry.grid(row=2, column=0, padx=5, pady=(2, 5), sticky="ew")
        self.voice_search_entry.bind("<KeyRelease>", self._on_voice_search)
        # FIX: CTkComboBox native tkinter.Menu kullanir; Windows'ta 300+ sesle
        # mouse-wheel ile kaymaz. Wheel destekli Listbox kullaniyoruz.
        voice_list_container = ctk.CTkFrame(voice_select_frame, fg_color="transparent")
        voice_list_container.grid(row=3, column=0, padx=5, pady=(0, 2), sticky="ew")
        voice_list_container.grid_columnconfigure(0, weight=1)
        self.voice_listbox = tk.Listbox(
            voice_list_container,
            height=6,
            exportselection=False,
            activestyle="none",
            selectmode=tk.SINGLE,
        )
        self.voice_listbox.grid(row=0, column=0, sticky="nsew")
        voice_list_container.grid_rowconfigure(0, weight=1)
        self.voice_list_scrollbar = ctk.CTkScrollbar(voice_list_container, command=self.voice_listbox.yview)
        self.voice_list_scrollbar.grid(row=0, column=1, sticky="ns")
        self.voice_listbox.configure(yscrollcommand=self.voice_list_scrollbar.set)
        self.voice_listbox.insert(0, "Loading voices...")
        self.voice_listbox.configure(state="disabled")
        self.voice_listbox.bind("<<ListboxSelect>>", self.voice_selected)
        # Mouse-wheel: Windows/macOS (<MouseWheel>) + Linux (<Button-4/5>)
        # Listbox uzerinde + scrollbar uzerinde (kullanici scrollbar'a gelip
        # cevirdiginde de liste kaysin diye ikisine de bagla).
        self.voice_listbox.bind("<MouseWheel>", self._on_voice_list_wheel)
        self.voice_listbox.bind("<Button-4>", self._on_voice_list_wheel)
        self.voice_listbox.bind("<Button-5>", self._on_voice_list_wheel)
        for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.voice_list_scrollbar.bind(_seq, self._on_voice_list_wheel)
            except Exception:
                pass
        # Geriye uyumluluk: eski kod voice_dropdown bekler
        self.voice_dropdown = self.voice_listbox
        self._apply_listbox_theme()
        # Favorite controls (kritik iyileştirme)
        fav_frame = ctk.CTkFrame(voice_select_frame, fg_color="transparent")
        fav_frame.grid(row=4, column=0, padx=5, pady=(0, 5), sticky="ew")
        fav_frame.grid_columnconfigure(0, weight=1)
        self.fav_btn = ctk.CTkButton(fav_frame, text="☆ Favori Ekle", width=110, height=22, fg_color="transparent", border_width=1, text_color=("gray10","gray90"), command=self.toggle_favorite, state="disabled")
        self.fav_btn.grid(row=0, column=0, padx=(0,5), sticky="ew")
        self.fav_only_checkbox = ctk.CTkCheckBox(fav_frame, text="Sadece ★", command=self._on_filter_change, font=ctk.CTkFont(size=11), width=90)
        self.fav_only_checkbox.grid(row=0, column=1, sticky="e")
        adj_frame = ctk.CTkFrame(controls_frame); adj_frame.grid(row=0, column=1, padx=(5, 0), pady=5, sticky="nsew")
        adj_frame.grid_columnconfigure(0, weight=1)
        rate_adj_frame = ctk.CTkFrame(adj_frame); rate_adj_frame.grid(row=0, column=0, padx=5, pady=(5,2), sticky="ew")
        rate_adj_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(rate_adj_frame, text="Rate:", font=ctk.CTkFont(size=11)).grid(row=0, column=0, padx=(5,0), pady=5, sticky="w")
        self.rate_slider = ctk.CTkSlider(rate_adj_frame, from_=-100, to=100, number_of_steps=40, command=self.update_rate_label); self.rate_slider.set(0); self.rate_slider.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.rate_value_label = ctk.CTkLabel(rate_adj_frame, text="0%", width=40, anchor="e"); self.rate_value_label.grid(row=0, column=2, padx=(0,5), pady=5, sticky="e")
        self.rate_reset_btn = ctk.CTkButton(rate_adj_frame, text="Reset", width=50, command=lambda: self.reset_slider("rate")); self.rate_reset_btn.grid(row=0, column=3, padx=(0,5), pady=5)
        pitch_adj_frame = ctk.CTkFrame(adj_frame); pitch_adj_frame.grid(row=1, column=0, padx=5, pady=(2,5), sticky="ew")
        pitch_adj_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(pitch_adj_frame, text="Pitch:", font=ctk.CTkFont(size=11)).grid(row=0, column=0, padx=(5,0), pady=5, sticky="w")
        self.pitch_slider = ctk.CTkSlider(pitch_adj_frame, from_=-50, to=50, number_of_steps=20, command=self.update_pitch_label); self.pitch_slider.set(0); self.pitch_slider.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.pitch_value_label = ctk.CTkLabel(pitch_adj_frame, text="0Hz", width=40, anchor="e"); self.pitch_value_label.grid(row=0, column=2, padx=(0,5), pady=5, sticky="e")
        self.pitch_reset_btn = ctk.CTkButton(pitch_adj_frame, text="Reset", width=50, command=lambda: self.reset_slider("pitch")); self.pitch_reset_btn.grid(row=0, column=3, padx=(0,5), pady=5)

        # --- Ses Profilleri (rate/pitch altindaki bos alan) ---
        # Her slot: ses adi (shortname) + rate + pitch saklar; yukle dugmesi
        # uygular, diskette profil adi + detay gosterir.
        profile_frame = ctk.CTkFrame(adj_frame)
        profile_frame.grid(row=2, column=0, padx=5, pady=(2, 5), sticky="nsew")
        profile_frame.grid_columnconfigure(0, weight=1)
        adj_frame.grid_rowconfigure(2, weight=1)
        profile_header = ctk.CTkFrame(profile_frame, fg_color="transparent")
        profile_header.grid(row=0, column=0, padx=5, pady=(5, 2), sticky="ew")
        profile_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(profile_header, text="📁 Ses Profilleri", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w")
        self.profile_clear_all_btn = ctk.CTkButton(profile_header, text="Temizle", width=70, height=22, fg_color="gray40", hover_color="gray30", command=self.clear_all_profiles, state="disabled")
        self.profile_clear_all_btn.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(profile_frame, text="💾: o anki ses+hız+perdeyi kaydet  •  ✕: profili sil",
                      font=ctk.CTkFont(size=10), text_color="gray60", anchor="w").grid(row=1, column=0, padx=5, pady=(0, 2), sticky="ew")
        self.profile_load_btns: dict[int, ctk.CTkButton] = {}
        self.profile_save_btns: dict[int, ctk.CTkButton] = {}
        self.profile_clear_btns: dict[int, ctk.CTkButton] = {}
        for _slot in range(1, MAX_VOICE_PROFILES + 1):
            _row = ctk.CTkFrame(profile_frame, fg_color="transparent")
            _row.grid(row=_slot + 1, column=0, padx=5, pady=2, sticky="ew")
            _row.grid_columnconfigure(0, weight=1)
            _load = ctk.CTkButton(_row, text=f"Boş Profil {_slot}", anchor="w", height=40,
                                  font=ctk.CTkFont(size=11),
                                  command=lambda s=_slot: self.load_profile(s), state="disabled")
            _load.grid(row=0, column=0, padx=(0, 5), sticky="ew")
            _save = ctk.CTkButton(_row, text="💾", width=38, height=40,
                                  fg_color="gray40", hover_color="gray30",
                                  command=lambda s=_slot: self.save_profile(s), state="disabled")
            _save.grid(row=0, column=1, padx=(0, 5))
            _clear = ctk.CTkButton(_row, text="✕", width=32, height=40,
                                   fg_color="transparent", border_width=1,
                                   text_color=("gray10", "gray90"),
                                   command=lambda s=_slot: self.clear_profile(s), state="disabled")
            _clear.grid(row=0, column=2)
            self.profile_load_btns[_slot] = _load
            self.profile_save_btns[_slot] = _save
            self.profile_clear_btns[_slot] = _clear
        self.refresh_profile_buttons()

        # --- Generate Button ---
        self.generate_btn = ctk.CTkButton(self, text="Generate Speech", command=self.start_generate_speech_thread, height=40, font=ctk.CTkFont(size=14, weight="bold"), state="disabled")
        self.generate_btn.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
        # Generate helper text (kritik: neden disabled)
        self.generate_helper_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color="gray60", anchor="w")
        self.generate_helper_label.grid(row=5, column=0, padx=20, pady=(0, 2), sticky="ew")

        # --- Player Controls ---
        # [Player controls setup remains the same as before]
        self.player_frame = ctk.CTkFrame(self)
        self.player_frame.grid(row=6, column=0, padx=20, pady=5, sticky="ew")
        self.player_frame.grid_columnconfigure(4, weight=1) # Progress slider column expands
        self.rewind_btn = ctk.CTkButton(self.player_frame, text=f"<< {SEEK_INTERVAL_SECONDS}s", width=60, command=lambda: self.seek_relative(-SEEK_INTERVAL_SECONDS), state="disabled")
        self.rewind_btn.grid(row=0, column=0, padx=(10, 5), pady=10)
        self.play_pause_btn = ctk.CTkButton(self.player_frame, text="▶ Play", width=80, command=self.toggle_play_pause, state="disabled")
        self.play_pause_btn.grid(row=0, column=1, padx=5, pady=10)
        self.stop_btn = ctk.CTkButton(self.player_frame, text="⏹ Stop", width=80, command=self.stop_audio, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=5, pady=10)
        self.forward_btn = ctk.CTkButton(self.player_frame, text=f"{SEEK_INTERVAL_SECONDS}s >>", width=60, command=lambda: self.seek_relative(SEEK_INTERVAL_SECONDS), state="disabled")
        self.forward_btn.grid(row=0, column=3, padx=5, pady=10)
        self.progress_slider = ctk.CTkSlider(self.player_frame, from_=0, to=100, state="disabled")
        self.progress_slider.set(0)
        self.progress_slider.grid(row=0, column=4, padx=5, pady=10, sticky="ew")
        self.progress_slider.bind("<ButtonRelease-1>", self.seek_audio_on_release) # On slider release
        self.progress_slider.bind("<ButtonPress-1>", self.pause_updates_on_drag)   # On slider press
        self.time_label = ctk.CTkLabel(self.player_frame, text="00:00 / 00:00", width=90, font=ctk.CTkFont(size=10), anchor="e")
        self.time_label.grid(row=0, column=5, padx=(0, 10), pady=10, sticky="e")

        # Volume controls (kritik)
        volume_frame = ctk.CTkFrame(self.player_frame, fg_color="transparent")
        volume_frame.grid(row=1, column=0, columnspan=6, padx=5, pady=(0, 8), sticky="ew")
        volume_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(volume_frame, text="Vol:", font=ctk.CTkFont(size=11)).grid(row=0, column=0, padx=(5,5))
        self.volume_slider = ctk.CTkSlider(volume_frame, from_=0, to=100, number_of_steps=20, command=self.on_volume_change)
        self.volume_slider.set(100)
        self.volume_slider.grid(row=0, column=1, padx=5, sticky="ew")
        self.volume_label = ctk.CTkLabel(volume_frame, text="100%", width=35, font=ctk.CTkFont(size=10))
        self.volume_label.grid(row=0, column=2, padx=(0,5))

        # FIX: slider'lar uzerinde mouse-wheel ile ince ayar
        for _slider, _step in (
            (self.rate_slider, 5.0),
            (self.pitch_slider, 2.5),
            (self.volume_slider, 5.0),
        ):
            try:
                self._bind_wheel_to_slider(_slider, _step)
            except Exception:
                pass

        # --- Save Button ---
        self.save_btn = ctk.CTkButton(self, text="Save Audio as MP3", command=self.save_audio, height=40, font=ctk.CTkFont(size=14), state="disabled")
        self.save_btn.grid(row=7, column=0, padx=20, pady=5, sticky="ew")

        # --- Status Label ---
        self.status_label = ctk.CTkLabel(self, text="Status: Initializing...", height=25, anchor="w")
        self.status_label.grid(row=8, column=0, padx=20, pady=(5, 10), sticky="ew")

    # --- Textbox Placeholder Logic ---
    def _fetch_default_textbox_color(self):
        """Fetches and stores the default text color of the textbox."""
        if hasattr(self, 'textbox') and self.textbox.winfo_exists():
            try:
                # Ensure the widget is fully ready
                self.textbox.update_idletasks()
                self.default_textbox_color = self.textbox.cget("text_color")
                print(f"INFO: Default textbox color fetched: {self.default_textbox_color}")
                # If placeholder is currently active, ensure its color is correct
                # This can happen if fetch was delayed past initial set
                if self.textbox_placeholder_active:
                    self.textbox.configure(text_color=TEXTBOX_PLACEHOLDER_COLOR)

            except Exception as e:
                print(f"WARN: Could not fetch default textbox color: {e}")
                # Fallback (might not match theme perfectly)
                current_mode = ctk.get_appearance_mode()
                self.default_textbox_color = "#DCE4EE" if current_mode == "Dark" else "#111111" # Near-white for Dark, Near-black for Light
                print(f"INFO: Using fallback textbox color for {current_mode} mode: {self.default_textbox_color}")
        else:
            # Reschedule if textbox doesn't exist or color isn't ready yet
             self.after(50, self._fetch_default_textbox_color)


    def _set_initial_textbox_placeholder(self):
        """Acilista ornek metni yerlestirir (bossa placeholder degil).

        Ornek metin gercek iceriktir: karakter sayilir, Generate ile
        hemen seslendirilebilir. Ilk gercek girdide otomatik silinir.
        """
        if not hasattr(self, 'textbox') or not self.textbox.winfo_exists():
            self.after(50, self._set_initial_textbox_placeholder) # Retry if widget not ready
            return
        # Ensure default color is fetched before proceeding
        if self.default_textbox_color is None:
            print("INFO: Waiting for default text color fetch before setting placeholder...")
            self.after(50, self._set_initial_textbox_placeholder) # Retry shortly
            return

        # Handle disabled state for placeholder insertion
        # (ic widget kapaliyken insert sessizce basarisiz olur)
        was_disabled = self._textbox_writable()
        current_text = self.textbox.get("1.0", "end-1c").strip()
        if not current_text:
            self.textbox_placeholder_active = False
            self.textbox_default_sample_active = True
            self.textbox.insert("1.0", DEFAULT_SAMPLE_TEXT)
            if self.default_textbox_color:
                self.textbox.configure(text_color=self.default_textbox_color)
            print("INFO: Initial textbox sample text set.")
        self.after(10, self.update_char_counter)
        self.after(20, self._update_ui_after_text_change)
        self._textbox_restore(was_disabled)

    def _clear_default_sample(self):
        """Acilis ornek metni ekrandaysa sessizce kaldirir."""
        if getattr(self, 'textbox_default_sample_active', False):
            try:
                if hasattr(self, 'textbox') and self.textbox.winfo_exists():
                    _was = self._textbox_writable()
                    try:
                        self.textbox.delete("1.0", ctk.END)
                    finally:
                        self._textbox_restore(_was)
            except Exception:
                pass
            self.textbox_default_sample_active = False

    def _on_textbox_paste_sample(self, event=None):
        """Ctrl+V ile yapistirmada ornek metin uzerine eklenmesin."""
        self._clear_default_sample()
        return None # Varsayilan yapistirma davranisi sursun

    def _on_textbox_sample_key(self, event=None):
        """Ilk gercek karakterde acilis ornegini temizler.

        Tus basilIRKEN calisir (yerlestirmeden once), boylece yazilan
        karakter bos kutuya duser. Gezinti/kisayol tuslari ornegi korur.
        """
        if not getattr(self, 'textbox_default_sample_active', False):
            return
        try:
            ch = getattr(event, 'char', '') or ''
            state = int(getattr(event, 'state', 0) or 0)
        except Exception:
            return
        # Yazdirilabilir tek karakter + Ctrl/Alt yoksa -> kullanici yaziyor
        if len(ch) == 1 and ch.isprintable() and not (state & 0xC):
            self._clear_default_sample()

    def _textbox_writable(self) -> bool:
        """Programatik duzenleme icin kutuyu yazilabilir yapar.

        CTkTextbox.cget("state") desteklenmedigi icin (ValueError verir)
        dogrudan ic Text widget'in durumuna bakilir. Kutu kapaliysa
        gecici acar ve True doner; cagiran is bitince
        _textbox_restore ile eski haline dondurmelidir.
        """
        was_disabled = False
        try:
            inner = getattr(getattr(self, 'textbox', None), '_textbox', None)
            if inner is not None:
                was_disabled = str(inner.cget("state")) == "disabled"
        except Exception:
            was_disabled = False
        if was_disabled:
            try:
                self.textbox.configure(state="normal")
            except Exception:
                pass
        return was_disabled

    def _textbox_restore(self, was_disabled: bool):
        """_textbox_writable ile acilan kutuyu gerekiyorsa kapatir."""
        if was_disabled:
            try:
                if hasattr(self, 'textbox') and self.textbox.winfo_exists():
                    self.textbox.configure(state="disabled")
            except Exception:
                pass


    def _on_textbox_focus_in(self, event=None):
        """Handles the textbox gaining focus."""
        if not hasattr(self, 'textbox') or not self.default_textbox_color: return
        # Don't clear if disabled (ic widget durumuna bakilir)
        try:
            _inner = getattr(self.textbox, "_textbox", None)
            if _inner is not None and str(_inner.cget("state")) == "disabled":
                return
        except Exception:
            pass
        if self.textbox_placeholder_active:
            self.textbox_placeholder_active = False
            self.textbox.delete("1.0", ctk.END)
            self.textbox.configure(text_color=self.default_textbox_color)
            self.after(10, self.update_char_counter)
            self.after(20, self._update_ui_after_text_change)

    def _on_textbox_focus_out(self, event=None):
        """Handles the textbox losing focus."""
        if not hasattr(self, 'textbox') or not self.default_textbox_color: return
        # Use after_idle to allow potential other focus-out events to process first
        self.after_idle(self._check_and_set_placeholder)

    def _check_and_set_placeholder(self):
        """Checks if textbox is empty after focus-out and sets placeholder."""
        if not hasattr(self, 'textbox') or not self.default_textbox_color: return
        # Verify focus is actually lost from textbox (important!)
        if self.focus_get() != self.textbox:
            was_disabled = self._textbox_writable()
            current_text = self.textbox.get("1.0", "end-1c").strip()
            if not current_text:
                self.textbox_placeholder_active = True
                self.textbox.insert("1.0", TEXTBOX_PLACEHOLDER_TEXT)
                self.textbox.configure(text_color=TEXTBOX_PLACEHOLDER_COLOR)
                self.after(10, self.update_char_counter)
                self.after(20, self._update_ui_after_text_change)
            self._textbox_restore(was_disabled)

    def get_input_text(self) -> str:
        """Gets the text from the textbox, excluding the placeholder."""
        if not hasattr(self, 'textbox'):
            return ""
        if self.textbox_placeholder_active:
            return ""
        else:
            return self.textbox.get("1.0", "end-1c").strip()

    def clear_text(self):
        """Clears the textbox and resets placeholder/counter."""
        if not hasattr(self, 'textbox') or not self.textbox.winfo_exists():
            return
        # Ensure textbox is writable even if disabled
        was_disabled = self._textbox_writable()
        self.textbox.delete("1.0", ctk.END)
        self.textbox_placeholder_active = False
        self.textbox_default_sample_active = False
        self._check_and_set_placeholder()
        self.update_char_counter()
        self._update_ui_after_text_change()
        self.update_status("Metin temizlendi.")
        if was_disabled:
            # Restore will be handled by set_ui_state, but ensure after call
            self.after(10, lambda: self.set_ui_state(self.check_current_audio_state()))

    def paste_from_clipboard(self):
        """Pastes clipboard content into textbox."""
        try:
            clip_text = self.clipboard_get()
        except Exception:
            self.update_status("⚠️ Pano boş veya okunamadı.")
            return
        if not clip_text:
            return
        was_disabled = self._textbox_writable()
        # If placeholder/sample active, clear it first
        if self.textbox_placeholder_active:
            self.textbox.delete("1.0", ctk.END)
            self.textbox_placeholder_active = False
            if self.default_textbox_color:
                self.textbox.configure(text_color=self.default_textbox_color)
        self._clear_default_sample()
        self.textbox.insert(ctk.INSERT, clip_text)
        self.update_char_counter()
        self._update_ui_after_text_change()
        self.update_status(f"📋 Panodan {len(clip_text)} karakter yapıştırıldı.")
        if was_disabled:
            self.after(10, lambda: self.set_ui_state(self.check_current_audio_state()))

    def update_char_counter(self):
        """Updates character and word counter label (10k Edge TTS limit)."""
        if not hasattr(self, 'char_counter_label') or not self.char_counter_label.winfo_exists():
            return
        text = self.get_input_text()
        char_count = len(text)
        word_count = len(text.split()) if text else 0
        # Color logic: gray <8k, orange 8k-10k, red >10k
        if char_count == 0:
            color = "gray60"
        elif char_count > 10000:
            color = "#E53935"  # red
        elif char_count > 8000:
            color = "#FB8C00"  # orange
        else:
            color = "gray60"
        self.char_counter_label.configure(text=f"{char_count:,} / 10.000 karakter  •  {word_count:,} kelime".replace(",", "."), text_color=color)

    # --- Theme Toggle ---
    def _toggle_theme_override(self):
        """
        Switches the appearance mode explicitly to Light or Dark,
        overriding the initial "System" setting.
        """
        if hasattr(self, 'theme_switch'):
            switch_state = self.theme_switch.get() # 1 for ON (Dark), 0 for OFF (Light)
            new_mode = "Dark" if switch_state == 1 else "Light"
            # Explicitly set the mode, stopping system following
            ctk.set_appearance_mode(new_mode)
            print(f"INFO: Appearance mode explicitly set to '{new_mode}' (overriding System).")
            self.schedule_settings_save()

            # Update default color after theme change (needs a slight delay)
            if hasattr(self, 'textbox'):
                 self.after(50, self._update_textbox_colors_after_theme_change)
        else:
            print("WARN: Theme switch not available.")

    def _update_textbox_colors_after_theme_change(self):
        """Updates textbox color refs and re-applies placeholder if needed."""
        print("INFO: Updating textbox colors after theme change...")
        self._fetch_default_textbox_color() # Re-fetch the potentially new default color
        # The fetch function now handles applying placeholder color if active
        try:
            self._apply_listbox_theme()
        except Exception:
            pass
        try:
            if hasattr(self, 'lang_filter_combo') and hasattr(self.lang_filter_combo, 'refresh_theme'):
                self.lang_filter_combo.refresh_theme()
        except Exception:
            pass

    def _update_theme_switch_state(self):
        """Sets the theme switch state based on the *current effective* appearance mode."""
        if hasattr(self, 'theme_switch') and self.theme_switch.winfo_exists():
            try:
                # Use get_appearance_mode() which returns the resolved mode ("Light" or "Dark")
                current_mode = ctk.get_appearance_mode()
                print(f"INFO: Updating theme switch state for effective mode: '{current_mode}'")
                if current_mode == "Dark":
                    self.theme_switch.select() # Turn switch ON
                else:
                    self.theme_switch.deselect() # Turn switch OFF
            except Exception as e:
                 print(f"WARN: Error updating theme switch state: {e}")
        else:
            # Reschedule if switch doesn't exist yet
            self.after(100, self._update_theme_switch_state)
            print("WARN: Theme switch not available for state update yet, rescheduling.")

    # --- UI & State Update Methods ---
    def update_status(self, message: str):
        """Updates the text in the bottom status bar."""
        if hasattr(self, 'status_label'):
            self.status_label.configure(text=f"Status: {message}")

    def _on_textbox_change(self, event=None):
        """Called when text is typed in the textbox to update UI state."""
        # Use after_idle to ensure the text change is processed first
        self.after_idle(self._update_ui_after_text_change)

    def _update_ui_after_text_change(self):
        """Updates UI state after text changes, maintaining current state context."""
        if not hasattr(self, 'textbox'):
            return

        self.update_char_counter()

        current_state = self.check_current_audio_state()

        # Update UI state which will check text and enable/disable generate button
        self.set_ui_state(current_state)

    def check_current_audio_state(self):
        """ Determine current state based on existing conditions."""
        current_state = 'idle'  # Default
        # Check if we have generated audio
        if self.audio_file_path and os.path.exists(self.audio_file_path):
            if self.just_playback_initialized and self.player:
                if self.player.playing:
                    current_state = 'playing'
                elif self.player.paused:
                    current_state = 'paused'
                else:
                    current_state = 'generated'
            else:
                current_state = 'generated'
        return current_state

    def set_ui_state(self, state: str):
        """Sets the enabled/disabled state of UI widgets based on application state."""
        # Uretim surerken hicbir cagrici Generate'i yeniden acamasin:
        # bayrak varken gorunum her zaman 'generating' kalir. Metin kutusu
        # ve diger kontroller bu durumda ACIK tutulur (asagida), yalnizca
        # Generate dugmesi (can_generate) kilitli kalir.
        if getattr(self, "_generating", False):
            state = "generating"
        is_player_ready = bool(self.just_playback_initialized and self.player)
        is_audio_loaded = bool(is_player_ready and self.audio_file_path and os.path.exists(self.audio_file_path) and self.audio_duration > 0.001)

        is_playing = is_player_ready and self.player.playing
        is_paused = is_player_ready and self.player.paused
        is_idle = not is_playing and not is_paused # Idle/stopped condition

        # Determine capabilities based on state
        can_press_play_pause = is_audio_loaded and state not in ['generating', 'loading']
        can_stop = is_audio_loaded and (is_playing or is_paused)
        can_seek = is_audio_loaded and (is_playing or is_paused)
        can_save = is_audio_loaded and is_idle # Can save only when idle/stopped

        voices_loaded = bool(self.voices_dict)
        has_input_text = bool(self.get_input_text())
        try:
            char_count_early = len(self.get_input_text())
        except Exception:
            char_count_early = 0

        # Add proper voice selection validation
        if hasattr(self, '_get_selected_voice'):
            selected_voice = self._get_selected_voice()
        elif hasattr(self, 'voice_dropdown'):
            try:
                selected_voice = self.voice_dropdown.get()
            except Exception:
                selected_voice = ""
        else:
            selected_voice = ""
        has_valid_voice = (voices_loaded and
                           selected_voice and
                           selected_voice in self.voices_dict and
                           "Loading" not in selected_voice and
                           "No match" not in selected_voice and
                           "No voices" not in selected_voice)

        # FIX: Edge TTS ~10k karakter limiti - asimda Generate bloklanir
        # (Onceki surumde helper kirmizi uyariyordu ama buton aktif kaliyordu.)
        can_generate = (has_valid_voice and has_input_text and char_count_early <= 10000
                        and state not in ['loading', 'generating', 'playing', 'error_no_audio'])
        # Uretim surerken de yazmaya/ayar degistirmeye izin ver: uretim
        # baslarken metin/ses/hiz snapshot alindigi icin ara degisiklikler
        # suren uretimi bozmaz, sonraki uretime yansir. Kilitli kalan tek
        # sey Generate dugmesinin kendisidir (mukerrer uretimi onler).
        can_load_text = state not in ['loading', 'playing', 'error_no_audio']
        controls_active = state not in ['loading', 'error_no_audio']
        # Theme switch should always be active
        theme_switch_state = ctk.NORMAL

        # Determine widget states (ON/OFF)
        play_pause_btn_state = ctk.NORMAL if can_press_play_pause else ctk.DISABLED
        stop_btn_state = ctk.NORMAL if can_stop else ctk.DISABLED
        seek_btns_state = ctk.NORMAL if can_seek else ctk.DISABLED
        progress_slider_state = ctk.NORMAL if can_seek else ctk.DISABLED
        save_btn_state = ctk.NORMAL if can_save else ctk.DISABLED
        generate_btn_state = ctk.NORMAL if can_generate else ctk.DISABLED
        load_file_btn_state = ctk.NORMAL if can_load_text else ctk.DISABLED
        voice_ctrl_state = ctk.NORMAL if voices_loaded and controls_active else ctk.DISABLED
        adj_ctrl_state = ctk.NORMAL if controls_active else ctk.DISABLED
        # Textbox state should generally be normal unless globally disabled
        textbox_state = ctk.NORMAL if controls_active else ctk.DISABLED

        # Determine dynamic button texts
        generate_btn_text = "Generate Speech"
        if state == 'loading': generate_btn_text = "Loading Voices..."
        elif state == 'generating': generate_btn_text = "Generating..."
        elif state == 'error_no_audio': generate_btn_text = "Audio Error"

        # Generate helper text (kritik)
        helper_text = ""
        helper_color = "gray60"
        char_count = len(self.get_input_text())
        if state == 'loading':
            helper_text = "Sesler yükleniyor, lütfen bekleyin..."
            helper_color = "gray60"
        elif state == 'generating':
            helper_text = "Ses oluşturuluyor... yazmaya devam edebilirsiniz"
            helper_color = "#1E88E5"
        elif state == 'playing':
            helper_text = "Oynatiliyor - durdurduktan sonra yeni ses olusturabilirsiniz"
            helper_color = "gray60"
        elif state == 'error_no_audio':
            helper_text = "Ses motoru hatasi"
            helper_color = "#E53935"
        elif char_count > 10000:
            helper_text = f"Metin cok uzun ({char_count:,} / 10.000) - kisaltin".replace(",", ".")
            helper_color = "#E53935"
        elif not has_input_text and not has_valid_voice:
            helper_text = "Metin girin ve ses secin"
            helper_color = "#FB8C00"
        elif not has_input_text:
            helper_text = "Metin girin - karakter sayaci ustte"
            helper_color = "#FB8C00"
        elif not voices_loaded:
            helper_text = "Sesler henuz yuklenmedi"
            helper_color = "#FB8C00"
        elif not has_valid_voice:
            helper_text = "Gecerli bir ses secin (filtreyi temizleyin)"
            helper_color = "#FB8C00"
        elif can_generate:
            # Ready state - show character count and selected voice short
            helper_text = f"Hazir - {char_count} karakter"
            helper_color = "#2E7D32"
            if has_valid_voice:
                _vname = selected_voice.split(' - ')[0].strip() if ' - ' in selected_voice else selected_voice.split('(')[0].strip()
                helper_text += f" - {_vname}"
        elif char_count > 8000:
            helper_text = f"Dikkat: {char_count} karakter (limit 10.000)"
            helper_color = "#FB8C00"

        play_pause_text = "▶ Play"
        if is_playing: play_pause_text = "⏸ Pause"
        elif is_paused: play_pause_text = "▶ Resume"

        # Apply states to widgets (use try-except for safety during init)
        try:
            # Use 'winfo_exists' for safety, especially during init/close
            if hasattr(self, 'theme_switch') and self.theme_switch.winfo_exists(): self.theme_switch.configure(state=theme_switch_state)
            if hasattr(self, 'voice_listbox') and self.voice_listbox.winfo_exists():
                try:
                    self.voice_listbox.configure(state=voice_ctrl_state)
                except Exception:
                    pass
            if hasattr(self, 'voice_search_entry') and self.voice_search_entry.winfo_exists(): self.voice_search_entry.configure(state=voice_ctrl_state)
            if hasattr(self, 'lang_filter_combo') and self.lang_filter_combo.winfo_exists(): self.lang_filter_combo.configure(state=voice_ctrl_state)
            if hasattr(self, 'gender_filter_combo') and self.gender_filter_combo.winfo_exists(): self.gender_filter_combo.configure(state=voice_ctrl_state)
            if hasattr(self, 'fav_btn') and self.fav_btn.winfo_exists(): self.fav_btn.configure(state=voice_ctrl_state)
            if hasattr(self, 'fav_only_checkbox') and self.fav_only_checkbox.winfo_exists(): self.fav_only_checkbox.configure(state=voice_ctrl_state)
            if hasattr(self, 'clear_btn') and self.clear_btn.winfo_exists(): self.clear_btn.configure(state=load_file_btn_state)
            if hasattr(self, 'paste_btn') and self.paste_btn.winfo_exists(): self.paste_btn.configure(state=load_file_btn_state)
            if hasattr(self, 'rate_slider') and self.rate_slider.winfo_exists(): self.rate_slider.configure(state=adj_ctrl_state)
            if hasattr(self, 'pitch_slider') and self.pitch_slider.winfo_exists(): self.pitch_slider.configure(state=adj_ctrl_state)
            if hasattr(self, 'volume_slider') and self.volume_slider.winfo_exists(): self.volume_slider.configure(state=adj_ctrl_state)
            if hasattr(self, 'rate_reset_btn') and self.rate_reset_btn.winfo_exists(): self.rate_reset_btn.configure(state=adj_ctrl_state)
            if hasattr(self, 'pitch_reset_btn') and self.pitch_reset_btn.winfo_exists(): self.pitch_reset_btn.configure(state=adj_ctrl_state)
            if hasattr(self, 'profile_load_btns'):
                for _b in self.profile_load_btns.values():
                    try:
                        if _b.winfo_exists(): _b.configure(state=voice_ctrl_state)
                    except Exception:
                        pass
            if hasattr(self, 'profile_save_btns'):
                for _b in self.profile_save_btns.values():
                    try:
                        if _b.winfo_exists(): _b.configure(state=voice_ctrl_state)
                    except Exception:
                        pass
            if hasattr(self, 'profile_clear_btns'):
                for _b in self.profile_clear_btns.values():
                    try:
                        if _b.winfo_exists(): _b.configure(state=adj_ctrl_state)
                    except Exception:
                        pass
            if hasattr(self, 'profile_clear_all_btn') and self.profile_clear_all_btn.winfo_exists(): self.profile_clear_all_btn.configure(state=adj_ctrl_state)
            if hasattr(self, 'textbox') and self.textbox.winfo_exists(): self.textbox.configure(state=textbox_state)
            if hasattr(self, 'load_file_btn') and self.load_file_btn.winfo_exists(): self.load_file_btn.configure(state=load_file_btn_state)
            if hasattr(self, 'generate_btn') and self.generate_btn.winfo_exists(): self.generate_btn.configure(state=generate_btn_state, text=generate_btn_text)
            if hasattr(self, 'generate_helper_label') and self.generate_helper_label.winfo_exists(): self.generate_helper_label.configure(text=helper_text, text_color=helper_color)
            if hasattr(self, 'save_btn') and self.save_btn.winfo_exists(): self.save_btn.configure(state=save_btn_state)

            if hasattr(self, 'play_pause_btn') and self.play_pause_btn.winfo_exists(): self.play_pause_btn.configure(state=play_pause_btn_state, text=play_pause_text)
            if hasattr(self, 'stop_btn') and self.stop_btn.winfo_exists(): self.stop_btn.configure(state=stop_btn_state)
            if hasattr(self, 'rewind_btn') and self.rewind_btn.winfo_exists(): self.rewind_btn.configure(state=seek_btns_state)
            if hasattr(self, 'forward_btn') and self.forward_btn.winfo_exists(): self.forward_btn.configure(state=seek_btns_state)
            if hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists(): self.progress_slider.configure(state=progress_slider_state)
            try:
                self.update_fav_button()
            except:
                pass
        except Exception as e:
            # This might happen during shutdown if widgets are destroyed
            if "application has been destroyed" not in str(e):
                 print(f"WARN: Error applying UI state '{state}': {e}")


    def reset_slider(self, slider_type: str):
        """Resets the Rate or Pitch slider to 0."""
        if slider_type == "rate":
            if hasattr(self, 'rate_slider'): self.rate_slider.set(0)
            self.update_rate_label(0)
        elif slider_type == "pitch":
            if hasattr(self, 'pitch_slider'): self.pitch_slider.set(0)
            self.update_pitch_label(0)
        self.schedule_settings_save()

    def update_rate_label(self, value: float):
        """Updates the Rate percentage label."""
        if hasattr(self, 'rate_value_label'):
             self.rate_value_label.configure(text=f"{int(float(value)):+d}%")
        # Slider suruklenirken her adimda disk yazmamak icin debounce'lu kaydet
        self.schedule_settings_save()

    def update_pitch_label(self, value: float):
        """Updates the Pitch Hertz label."""
        if hasattr(self, 'pitch_value_label'):
             self.pitch_value_label.configure(text=f"{int(float(value)):+d}Hz")
        self.schedule_settings_save()

    def on_volume_change(self, value: float):
        """Handles volume slider changes."""
        vol = max(0, min(100, int(float(value))))
        self.current_volume = vol / 100.0
        if hasattr(self, 'volume_label') and self.volume_label.winfo_exists():
            try:
                self.volume_label.configure(text=f"{vol}%")
            except:
                pass
        self.schedule_settings_save()
        if self.just_playback_initialized and self.player:
            try:
                self.player.set_volume(self.current_volume)
            except Exception as e:
                # Ignore if no file loaded yet - volume will be applied on next load
                if "no file" not in str(e).lower() and "not initialized" not in str(e).lower():
                    print(f"WARN: Could not set volume: {e}")

    def _on_voice_list_wheel(self, event=None):
        """Listbox'ta mouse tekerlegi ile kaydirma (Win/macOS/Linux)."""
        try:
            lb = self.voice_listbox
            if str(lb.cget("state")) == "disabled":
                return "break"
            if event is None:
                return "break"
            # Linux: Button-4 (yukari) / Button-5 (asagi)
            if getattr(event, "num", None) == 4:
                lb.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                lb.yview_scroll(1, "units")
            else:
                delta = getattr(event, "delta", 0)
                # Windows: delta=120 katlari; macOS: kucuk degerler
                steps = int(-1 * (delta / 120)) if delta else 0
                if steps == 0 and delta != 0:
                    steps = -1 if delta > 0 else 1
                lb.yview_scroll(steps, "units")
        except Exception:
            pass
        return "break"

    def _apply_listbox_theme(self):
        """Listbox renklerini aktif CTk temasina uydur."""
        if not hasattr(self, 'voice_listbox'):
            return
        try:
            mode = ctk.get_appearance_mode()
            if mode == "Dark":
                self.voice_listbox.configure(
                    bg="#2b2b2b", fg="#dce4ee",
                    selectbackground="#1f6aa5", selectforeground="white",
                    highlightbackground="#2b2b2b", highlightcolor="#2b2b2b",
                )
            else:
                self.voice_listbox.configure(
                    bg="white", fg="black",
                    selectbackground="#1f6aa5", selectforeground="white",
                    highlightbackground="white", highlightcolor="white",
                )
        except Exception:
            pass
        try:
            if hasattr(self, 'lang_filter_combo') and hasattr(self.lang_filter_combo, 'refresh_theme'):
                self.lang_filter_combo.refresh_theme()
        except Exception:
            pass

    def _get_selected_voice(self) -> str:
        """Listbox'ta secili sesin display adini dondur."""
        try:
            if not hasattr(self, 'voice_listbox'):
                return ""
            sel = self.voice_listbox.curselection()
            if not sel:
                return ""
            return self.voice_listbox.get(sel[0])
        except Exception:
            return ""

    def _set_selected_voice(self, name: str):
        """Listbox'ta verilen adi sec (yoksa ilk ogen)."""
        try:
            lb = self.voice_listbox
            was_disabled = str(lb.cget("state")) == "disabled"
            if was_disabled:
                lb.configure(state="normal")
            lb.selection_clear(0, tk.END)
            values = lb.get(0, tk.END)
            idx = values.index(name) if name in values else (0 if values else None)
            if idx is not None:
                lb.selection_set(idx)
                lb.activate(idx)
                lb.see(idx)
            if was_disabled:
                lb.configure(state="disabled")
        except Exception:
            pass

    def _set_voice_list_values(self, values: list, state=None):
        """Listbox icerigini toptan guncelle."""
        try:
            lb = self.voice_listbox
            lb.configure(state="normal")
            lb.delete(0, tk.END)
            for v in values:
                lb.insert(tk.END, v)
            if state is not None:
                lb.configure(state=state)
        except Exception as e:
            print(f"WARN: Could not update voice list: {e}")

    def _bind_wheel_to_slider(self, slider, step: float = 1.0):
        """Slider uzerinde mouse-wheel ile ince ayar."""
        def _on_wheel(event=None):
            try:
                if str(slider.cget("state")) == "disabled":
                    return "break"
                direction = 0
                if event is not None:
                    if getattr(event, "num", None) == 4:
                        direction = 1
                    elif getattr(event, "num", None) == 5:
                        direction = -1
                    else:
                        delta = getattr(event, "delta", 0)
                        direction = 1 if delta > 0 else (-1 if delta < 0 else 0)
                if direction:
                    new_val = max(slider.cget("from_"), min(slider.cget("to"), slider.get() + direction * step))
                    slider.set(new_val)
                    # CTkSlider.set() command callback'i tetiklemez; etiket + kayit manuel
                    try:
                        if slider is getattr(self, 'rate_slider', None):
                            self.update_rate_label(new_val)
                        elif slider is getattr(self, 'pitch_slider', None):
                            self.update_pitch_label(new_val)
                        elif slider is getattr(self, 'volume_slider', None):
                            self.on_volume_change(new_val)
                    except Exception:
                        pass
                return "break"
            except Exception:
                return "break"
        try:
            slider.bind("<MouseWheel>", _on_wheel)
            slider.bind("<Button-4>", _on_wheel)
            slider.bind("<Button-5>", _on_wheel)
        except Exception:
            pass

    def voice_selected(self, event=None, choice=None):
        """Callback when a voice is selected from the list. Updates the UI state."""
        # Listbox'tan cagrida event gelir, ComboBox uyumlulugu icin choice opsiyonel
        self.update_fav_button()
        current_state = self.check_current_audio_state()
        self.set_ui_state(current_state)
        self.schedule_settings_save()

    def load_favorites(self):
        """Loads favorite voices from JSON file (APPDATA, atomic-safe read)."""
        try:
            data = _read_json(FAVORITES_FILE, [])
            if isinstance(data, list):
                self.favorite_voices = set(data)
                print(f"INFO: Loaded {len(self.favorite_voices)} favorite voices from {FAVORITES_FILE}")
            else:
                self.favorite_voices = set()
        except Exception as e:
            print(f"WARN: Could not load favorites: {e}")
            self.favorite_voices = set()

    def save_favorites(self):
        """Saves favorite voices to JSON file (atomik yazma)."""
        ok = _atomic_write_json(FAVORITES_FILE, sorted(list(self.favorite_voices)))
        if ok:
            print(f"INFO: Saved {len(self.favorite_voices)} favorites to {FAVORITES_FILE}")
        else:
            print(f"WARN: Could not save favorites to {FAVORITES_FILE}")

    # --- Settings persistence (rate/pitch/volume/voice/filters/theme) ---
    def _collect_settings(self) -> dict:
        try:
            rate = int(float(self.rate_slider.get())) if hasattr(self, 'rate_slider') else 0
        except Exception:
            rate = 0
        try:
            pitch = int(float(self.pitch_slider.get())) if hasattr(self, 'pitch_slider') else 0
        except Exception:
            pitch = 0
        vol = getattr(self, 'current_volume', 1.0)
        try:
            lang = self.lang_filter_combo.get() if hasattr(self, 'lang_filter_combo') else "All Languages"
        except Exception:
            lang = "All Languages"
        try:
            gender = self.gender_filter_combo.get() if hasattr(self, 'gender_filter_combo') else "All"
        except Exception:
            gender = "All"
        try:
            fav_only = bool(self.fav_only_checkbox.get() == 1) if hasattr(self, 'fav_only_checkbox') else False
        except Exception:
            fav_only = False
        try:
            sel_display = self._get_selected_voice()
            sel_short = self.voices_dict.get(sel_display)
        except Exception:
            sel_short = None
        try:
            theme = ctk.get_appearance_mode()
        except Exception:
            theme = "System"
        return {
            "rate": max(-100, min(100, rate)),
            "pitch": max(-50, min(50, pitch)),
            "volume": max(0.0, min(1.0, float(vol))),
            "lang_filter": lang,
            "gender_filter": gender if gender in ("All", "Male", "Female") else "All",
            "fav_only": fav_only,
            "selected_voice": sel_short,
            "theme": theme if theme in ("Light", "Dark") else "System",
        }

    def save_settings(self):
        try:
            data = self._collect_settings()
            self.app_settings = data
            if _atomic_write_json(SETTINGS_FILE, data):
                print(f"INFO: Saved settings to {SETTINGS_FILE}: rate={data['rate']} pitch={data['pitch']} vol={data['volume']:.2f}")
        except Exception as e:
            print(f"WARN: Could not save settings: {e}")

    def schedule_settings_save(self, delay_ms: int = 400):
        """Slider/filter degisikliklerinde disk yazmayi debounce'la."""
        try:
            if getattr(self, '_settings_after_id', None):
                try:
                    self.after_cancel(self._settings_after_id)
                except Exception:
                    pass
                self._settings_after_id = None
            if self.winfo_exists():
                self._settings_after_id = self.after(delay_ms, self._flush_settings_save)
        except Exception:
            pass

    def _flush_settings_save(self):
        self._settings_after_id = None
        try:
            if self.winfo_exists():
                self.save_settings()
        except Exception:
            pass

    def _apply_initial_settings_to_widgets(self):
        """Acilista kaydedilmis rate/pitch/volume degerlerini slider'lara uygula."""
        s = getattr(self, 'app_settings', {}) or {}
        try:
            rate = int(s.get("rate", 0))
            rate = max(-100, min(100, rate))
        except Exception:
            rate = 0
        try:
            pitch = int(s.get("pitch", 0))
            pitch = max(-50, min(50, pitch))
        except Exception:
            pitch = 0
        try:
            vol_pct = int(round(float(s.get("volume", 1.0)) * 100))
            vol_pct = max(0, min(100, vol_pct))
        except Exception:
            vol_pct = 100
        try:
            if hasattr(self, 'rate_slider'):
                self.rate_slider.set(rate)
            self.update_rate_label(rate)
        except Exception:
            pass
        try:
            if hasattr(self, 'pitch_slider'):
                self.pitch_slider.set(pitch)
            self.update_pitch_label(pitch)
        except Exception:
            pass
        try:
            if hasattr(self, 'volume_slider'):
                self.volume_slider.set(vol_pct)
            # on_volume_change zaten current_volume + label gunceller
            self.on_volume_change(vol_pct)
        except Exception:
            pass
        # schedule_settings_save'in tetikledigi gereksiz yazmayi iptal et
        try:
            if getattr(self, '_settings_after_id', None):
                try:
                    self.after_cancel(self._settings_after_id)
                except Exception:
                    pass
                self._settings_after_id = None
        except Exception:
            pass
        print(f"INFO: Restored settings: rate={rate} pitch={pitch} volume={vol_pct}%")

    def _apply_voice_related_settings(self):
        """Ses listesi yuklendikten sonra dil/cinsiyet/fav/secili sesi geri yukle."""
        s = getattr(self, 'app_settings', {}) or {}
        # Gender
        try:
            g = s.get("gender_filter", "All")
            if g in ("All", "Male", "Female") and hasattr(self, 'gender_filter_combo'):
                self.gender_filter_combo.set(g)
        except Exception:
            pass
        # Fav-only
        try:
            if s.get("fav_only") and hasattr(self, 'fav_only_checkbox'):
                self.fav_only_checkbox.select()
            elif hasattr(self, 'fav_only_checkbox'):
                self.fav_only_checkbox.deselect()
        except Exception:
            pass
        # Language (deger listede yoksa All Languages'e dus)
        try:
            lang = s.get("lang_filter", "All Languages")
            if hasattr(self, 'lang_filter_combo'):
                vals = []
                try:
                    vals = self.lang_filter_combo.cget("values")
                except Exception:
                    vals = []
                self.lang_filter_combo.set(lang if lang in vals else "All Languages")
        except Exception:
            pass
        # Filtreleri uygula, sonra kayitli sesi sec
        try:
            self._apply_voice_filters()
        except Exception:
            pass
        pending = getattr(self, '_pending_voice_shortname', None)
        if pending:
            try:
                target_display = self._select_voice_by_shortname(pending)
                if target_display is not None:
                    try:
                        self.update_fav_button()
                        self.set_ui_state(self.check_current_audio_state())
                    except Exception:
                        pass
                    print(f"INFO: Restored selected voice: {target_display}")
            except Exception as e:
                print(f"WARN: Could not restore selected voice: {e}")
            finally:
                self._pending_voice_shortname = None

    def is_favorite(self, display_name: str) -> bool:
        """Checks if a display name is favorited."""
        short = self.voices_dict.get(display_name)
        return short in self.favorite_voices if short else False

    def toggle_favorite(self):
        """Toggles favorite status for currently selected voice."""
        display = self._get_selected_voice()
        if not display or display in ["Loading voices...", "No match found", "No voices found"]:
            self.update_status("⚠️ Favori için önce bir ses seçin.")
            return
        short = self.voices_dict.get(display)
        if not short:
            return
        if short in self.favorite_voices:
            self.favorite_voices.remove(short)
            self.update_status(f"☆ Favoriden cikarildi: {display.split(' - ')[0].strip() if ' - ' in display else display.split('(')[0].strip()}")
        else:
            self.favorite_voices.add(short)
            self.update_status(f"★ Favoriye eklendi: {display.split(' - ')[0].strip() if ' - ' in display else display.split('(')[0].strip()}")
        self.save_favorites()
        self.update_fav_button()
        # Re-apply filter if fav-only is active
        if hasattr(self, 'fav_only_checkbox') and self.fav_only_checkbox.get() == 1:
            self._apply_voice_filters()

    def update_fav_button(self):
        """Updates fav button text based on current selection."""
        if not hasattr(self, 'fav_btn') or not self.fav_btn.winfo_exists():
            return
        display = self._get_selected_voice()
        if self.is_favorite(display):
            self.fav_btn.configure(text="★ Favoriden Çıkar", fg_color="#FFD700", text_color="black", hover_color="#E6C200")
        else:
            self.fav_btn.configure(text="☆ Favori Ekle", fg_color="transparent", text_color=("gray10","gray90"), hover_color=("gray80","gray20"))

    # --- Ses Profilleri (ses adi + rate + pitch) ---
    def load_voice_profiles(self):
        """profiles.json'dan kayitli profilleri okur (yoksa bos baslar)."""
        try:
            data = _read_json(PROFILES_FILE, {})
            cleaned: dict[str, dict] = {}
            if isinstance(data, dict):
                for key, val in data.items():
                    try:
                        slot = int(key)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= slot <= MAX_VOICE_PROFILES and isinstance(val, dict):
                        try:
                            rate = max(-100, min(100, int(val.get("rate", 0))))
                        except (TypeError, ValueError):
                            rate = 0
                        try:
                            pitch = max(-50, min(50, int(val.get("pitch", 0))))
                        except (TypeError, ValueError):
                            pitch = 0
                        cleaned[str(slot)] = {
                            "name": str(val.get("name", f"Profil {slot}"))[:30] or f"Profil {slot}",
                            "voice": str(val.get("voice", "")),
                            "voice_label": str(val.get("voice_label", ""))[:60],
                            "rate": rate,
                            "pitch": pitch,
                        }
            self.voice_profiles = cleaned
            print(f"INFO: Loaded {len(cleaned)} voice profiles from {PROFILES_FILE}")
        except Exception as e:
            print(f"WARN: Could not load voice profiles: {e}")
            self.voice_profiles = {}

    def save_voice_profiles(self):
        """Profillleri atomik olarak profiles.json'a yazar."""
        if _atomic_write_json(PROFILES_FILE, self.voice_profiles):
            print(f"INFO: Saved {len(self.voice_profiles)} voice profiles to {PROFILES_FILE}")
        else:
            print(f"WARN: Could not save voice profiles to {PROFILES_FILE}")

    def _profile_auto_name(self, display: str, rate: int, pitch: int) -> str:
        """Kaydetmede oneri isim: 'Emel +10% +0Hz' gibi."""
        try:
            first = display.split(" - ")[0].strip() if " - " in display else display.split("(")[0].strip()
        except Exception:
            first = display
        return f"{first} {rate:+d}% {pitch:+d}Hz"[:30]

    def _profile_short_label(self, prof: dict) -> str:
        """Yukle dugmesinin ikinci satiri: 'Emel • +10% • +0Hz'."""
        label = str(prof.get("voice_label", ""))
        short = label.split(" - ")[0].strip() if " - " in label else label.split("(")[0].strip()
        if not short:
            short = str(prof.get("voice", ""))
        return f"{short} • {int(prof.get('rate', 0)):+d}% • {int(prof.get('pitch', 0)):+d}Hz"

    def refresh_profile_buttons(self):
        """Profil dugmelerinin yazi/gorunumunu kayitli veriye gore tazeler."""
        if not hasattr(self, 'profile_load_btns'):
            return
        for slot in range(1, MAX_VOICE_PROFILES + 1):
            prof = self.voice_profiles.get(str(slot))
            load_btn = self.profile_load_btns.get(slot)
            if load_btn is None or not load_btn.winfo_exists():
                continue
            try:
                if prof:
                    load_btn.configure(text=f"{prof.get('name', f'Profil {slot}')}\n{self._profile_short_label(prof)}")
                else:
                    load_btn.configure(text=f"Boş Profil {slot}")
            except Exception:
                pass

    def _current_voice_shortname(self) -> str | None:
        """Su an secili sesin shortname'i (gecersizse None)."""
        try:
            display = self._get_selected_voice()
            if not display or display in ("Loading voices...", "No match found", "No voices found"):
                return None
            return self.voices_dict.get(display)
        except Exception:
            return None

    def save_profile(self, slot: int):
        """O anki ses+rate+pitch'i slota kaydeder. Bos slota ilk kayitta isim sorar."""
        if slot < 1 or slot > MAX_VOICE_PROFILES:
            return
        short = self._current_voice_shortname()
        if not short:
            self.update_status("⚠️ Profil için önce geçerli bir ses seçin.")
            return
        try:
            rate = max(-100, min(100, int(float(self.rate_slider.get()))))
        except Exception:
            rate = 0
        try:
            pitch = max(-50, min(50, int(float(self.pitch_slider.get()))))
        except Exception:
            pitch = 0
        try:
            display = self._get_selected_voice()
        except Exception:
            display = short
        key = str(slot)
        existing = self.voice_profiles.get(key)
        if existing:
            name = str(existing.get("name", f"Profil {slot}"))
        else:
            suggested = self._profile_auto_name(display or short, rate, pitch)
            try:
                dlg = ctk.CTkInputDialog(text=f"Profil {slot} için isim girin:", title="Profil Kaydet")
                answer = dlg.get_input()
            except Exception as e:
                print(f"WARN: Profile name dialog failed: {e}")
                answer = None
            if answer is None or not str(answer).strip():
                self.update_status("Profil kaydetme iptal edildi.")
                return
            name = str(answer).strip()[:30]
        self.voice_profiles[key] = {
            "name": name,
            "voice": short,
            "voice_label": display or short,
            "rate": rate,
            "pitch": pitch,
        }
        self.save_voice_profiles()
        self.refresh_profile_buttons()
        action = "güncellendi" if existing else "kaydedildi"
        self.update_status(f"✅ Profil {slot} {action}: {name}")

    def load_profile(self, slot: int):
        """Slottaki ses+rate+pitch'i uygular."""
        prof = self.voice_profiles.get(str(slot))
        if not prof:
            self.update_status(f"Profil {slot} boş — önce 💾 ile kaydedin.")
            return
        try:
            rate = max(-100, min(100, int(prof.get("rate", 0))))
            pitch = max(-50, min(50, int(prof.get("pitch", 0))))
        except (TypeError, ValueError):
            rate, pitch = 0, 0
        try:
            if hasattr(self, 'rate_slider'):
                self.rate_slider.set(rate)
            self.update_rate_label(rate)
        except Exception:
            pass
        try:
            if hasattr(self, 'pitch_slider'):
                self.pitch_slider.set(pitch)
            self.update_pitch_label(pitch)
        except Exception:
            pass
        voice_ok = False
        short = str(prof.get("voice", ""))
        if short:
            try:
                found = self._select_voice_by_shortname(short)
                voice_ok = found is not None
            except Exception as e:
                print(f"WARN: Could not apply profile voice: {e}")
        try:
            self.update_fav_button()
            self.set_ui_state(self.check_current_audio_state())
        except Exception:
            pass
        self.schedule_settings_save()
        name = prof.get("name", f"Profil {slot}")
        if voice_ok:
            self.update_status(f"✅ Profil {slot} yüklendi: {name}")
        else:
            self.update_status(f"⚠️ Profil {slot}: hız/perde uygulandı ama '{short}' sesi bulunamadı.")

    def clear_profile(self, slot: int):
        """Slotu bosaltir ve dosyaya yazar."""
        key = str(slot)
        if key in self.voice_profiles:
            name = self.voice_profiles[key].get("name", f"Profil {slot}")
            del self.voice_profiles[key]
            self.save_voice_profiles()
            self.refresh_profile_buttons()
            self.update_status(f"🗑 Profil {slot} silindi: {name}")
        else:
            self.update_status(f"Profil {slot} zaten boş.")

    def clear_all_profiles(self):
        """Tum profilleri sifirlar."""
        if not self.voice_profiles:
            self.update_status("Silinecek profil yok — hepsi boş.")
            return
        self.voice_profiles = {}
        self.save_voice_profiles()
        self.refresh_profile_buttons()
        self.update_status("🗑 Tüm profiller temizlendi.")

    def _select_voice_by_shortname(self, shortname: str) -> str | None:
        """Shortname ile sesi bulup listede secer; filtre disindaysa
        filtreleri sifirlayip gorunur yapar. Basarida display adi doner."""
        if not shortname or not getattr(self, 'voices_dict', None):
            return None
        target_display = None
        for disp, short in self.voices_dict.items():
            if short == shortname:
                target_display = disp
                break
        if target_display is None:
            return None
        try:
            current_vals = list(self.voice_listbox.get(0, tk.END))
        except Exception:
            current_vals = []
        if target_display not in current_vals:
            try:
                self.lang_filter_combo.set("All Languages")
            except Exception:
                pass
            try:
                self.gender_filter_combo.set("All")
            except Exception:
                pass
            try:
                self.voice_search_entry.delete(0, tk.END)
            except Exception:
                pass
            try:
                self.fav_only_checkbox.deselect()
            except Exception:
                pass
            try:
                self._apply_voice_filters()
            except Exception:
                pass
        try:
            self._set_selected_voice(target_display)
        except Exception:
            pass
        return target_display

    def _filter_voices(self) -> list[str]:
        """Filters the list of voice display names based on search + language + gender."""
        if not hasattr(self, 'voice_search_entry'):
            return []
        try:
            raw_term = self.voice_search_entry.get() if hasattr(self, 'voice_search_entry') else ""
        except Exception:
            raw_term = ""
        search_term = _norm_search(raw_term)
        lang_display = self.lang_filter_combo.get() if hasattr(self, 'lang_filter_combo') else "All Languages"
        gender_filter = self.gender_filter_combo.get() if hasattr(self, 'gender_filter_combo') else "All"
        filtered = self._all_voice_display_names
        # Language filter (display name -> locale code). Unknown/empty value = no filtering.
        if lang_display != "All Languages" and lang_display in self.lang_display_to_locale:
            locale_code = self.lang_display_to_locale.get(lang_display, lang_display)
            filtered = [name for name in filtered if self.voice_display_to_raw.get(name, {}).get("Locale") == locale_code]
        # Gender filter. Only explicit Male/Female filters; anything else = no filtering.
        if gender_filter in ("Male", "Female"):
            filtered = [name for name in filtered if self.voice_display_to_raw.get(name, {}).get("Gender") == gender_filter]
        # Favorite filter
        fav_only = False
        if hasattr(self, 'fav_only_checkbox') and self.fav_only_checkbox.winfo_exists():
            try:
                fav_only = self.fav_only_checkbox.get() == 1
            except:
                fav_only = False
        if fav_only:
            filtered = [name for name in filtered if self.is_favorite(name)]
        # Search term filter (aksansiz: 'turk' -> 'Türk' eslesir)
        if search_term:
            filtered = [name for name in filtered if search_term in _norm_search(name)]
        return filtered

    def _on_filter_change(self, choice=None):
        """Called when language or gender filter changes."""
        self._apply_voice_filters()
        self.schedule_settings_save()

    def _apply_voice_filters(self):
        """Applies current filters and updates dropdown + helper."""
        if not hasattr(self, 'voice_listbox'):
            return
        try:
            if not self.voice_listbox.winfo_exists():
                return
        except Exception:
            return
        filtered_voices = self._filter_voices()
        current_selection = self._get_selected_voice()
        # Update filter result count in status? Optional
        if not filtered_voices:
            self._set_voice_list_values(["No match found"], state=ctk.DISABLED)
            self._set_selected_voice("No match found")
            current_state = self.check_current_audio_state()
            self.set_ui_state(current_state)
        else:
            self._set_voice_list_values(filtered_voices, state=ctk.NORMAL)
            if current_selection in filtered_voices:
                self._set_selected_voice(current_selection)
            else:
                self._set_selected_voice(filtered_voices[0])
            current_state = self.check_current_audio_state()
            self.set_ui_state(current_state)

    def _on_voice_search(self, event=None):
        """Arama kutusunda her tusa filtreyi yeniden kurmak yerine debounce uygula."""
        try:
            if getattr(self, '_search_after_id', None):
                try:
                    self.after_cancel(self._search_after_id)
                except Exception:
                    pass
                self._search_after_id = None
            self._search_after_id = self.after(150, self._flush_voice_search)
        except Exception:
            try:
                self._apply_voice_filters()
            except Exception:
                pass

    def _flush_voice_search(self):
        self._search_after_id = None
        try:
            if self.winfo_exists():
                self._apply_voice_filters()
        except Exception:
            pass

    # --- Asynchronous Operations & Threading ---
    def load_voices_async(self):
        """Starts a thread to load the voice list asynchronously."""
        self.set_ui_state('loading')
        self.update_status("Loading voice list...")
        thread = threading.Thread(target=self._run_async_task, args=(self._load_voices_task,), daemon=True)
        thread.start()

    def start_generate_speech_thread(self):
        """Starts a thread to generate TTS audio asynchronously."""
        self._delete_temp_audio_file() # Delete old temp file first
        if self.just_playback_initialized and self.player and (self.player.playing or self.player.paused):
             self.stop_audio() # Stop playback if currently active

        # Use the dedicated function to get input text, ignoring placeholder
        text = self.get_input_text()
        selected_voice_display = self._get_selected_voice()

        # Input validation
        if not text: # Check if actual text is empty
            self.update_status("❌ Error: Text input is empty."); self.set_ui_state('idle'); return
        if not selected_voice_display or "Loading" in selected_voice_display or "No match" in selected_voice_display or selected_voice_display not in self.voices_dict:
            self.update_status("❌ Error: Please select a valid voice."); self.set_ui_state('idle'); return

        voice_short_name = self.voices_dict[selected_voice_display]
        rate = int(self.rate_slider.get())
        pitch = int(self.pitch_slider.get())
        rate_str = f"{rate:+d}%"
        pitch_str = f"{pitch:+d}Hz"

        # Metin/ses/hiz snapshot'i thread'e tasinir; sonrasinda kullanici
        # yazmaya devam edebilir. Bayrak, bitis yollari temizleyene kadar
        # Generate'in yeniden acilmasini engeller.
        self._generating = True
        self.set_ui_state('generating')
        self.update_status("Generating audio...")
        thread = threading.Thread(target=self._run_async_task,
                                  args=(self._generate_audio_task, text, voice_short_name, rate_str, pitch_str),
                                  daemon=True)
        thread.start()

    def _run_async_task(self, coro, *args):
        """Runs an asyncio coroutine in a new event loop (suitable for threads)."""
        try:
            asyncio.run(coro(*args))
        except Exception as e:
            print(f"ERROR: Exception in async task thread: {e}")
            # Update status/state on the main thread (bayragi da temizler)
            self.after(0, self._on_generate_failed,
                       f"❌ Error during async operation: {e}")

    def _on_generate_failed(self, message: str):
        """Uretim/yukleme hatasinda ana thread'de calisir: bayragi indirir,
        mesaji yazar ve arayuzu idle'a alir."""
        self._generating = False
        self.update_status(message)
        self.set_ui_state('idle')

    async def _load_voices_task(self):
        """Coroutine to fetch the list of voices from edge-tts."""
        try:
            voices = await edge_tts.list_voices()
            # Sort by Locale, then ShortName for a structured display
            voices.sort(key=lambda v: (v['Locale'], v['ShortName']))
            self.voices_raw = voices
            # Build locale -> LocaleName mapping for clean language filter
            self.locale_to_lang_display = {}
            self.lang_display_to_locale = {}
            for v in voices:
                loc = v['Locale']
                loc_name = v.get('LocaleName', loc)
                if loc not in self.locale_to_lang_display:
                    self.locale_to_lang_display[loc] = loc_name
                    self.lang_display_to_locale[loc_name] = loc
            # Create clean display names (remove Microsoft/Online redundancy)
            def _clean(v):
                fname = v['FriendlyName']
                if fname.startswith("Microsoft "):
                    fname = fname[len("Microsoft "):]
                fname = fname.replace(" Online (Natural)", "").replace(" Online", "")
                return fname
            self.voices_dict = {_clean(v): v['ShortName'] for v in voices}
            self.voice_display_to_raw = {_clean(v): v for v in voices}
            self._all_voice_display_names = list(self.voices_dict.keys())
            # Update the UI on the main thread when done
            self.after(0, self._update_voice_dropdown_ui, self._all_voice_display_names)
        except Exception as e:
            print(f"ERROR: Failed to load voices: {e}")
            self.after(0, lambda: self.update_status(f"❌ Error loading voices: {e}"))
            self.after(0, lambda: self.set_ui_state('idle')) # Set to idle if loading fails

    def _update_voice_dropdown_ui(self, voice_list: list[str]):
        """Updates the voice Listbox on the main thread."""
        if not hasattr(self, 'voice_listbox') or not self.voice_listbox.winfo_exists(): return

        if voice_list:
            self._set_voice_list_values(voice_list, state=ctk.NORMAL)
            self._set_selected_voice(voice_list[0]) # Select the first voice by default
            if hasattr(self, 'voice_search_entry') and self.voice_search_entry.winfo_exists():
                 self.voice_search_entry.configure(state=ctk.NORMAL)
            # Populate language filter with friendly names (temiz arayuz)
            # ScrollableDropdown: values + state ayni configure cagrisi ile
            if hasattr(self, 'lang_filter_combo') and self.lang_filter_combo.winfo_exists():
                try:
                    lang_names = sorted(set(self.locale_to_lang_display.values()))
                    lang_values = ["All Languages"] + lang_names
                    self.lang_filter_combo.configure(values=lang_values, state=ctk.NORMAL)
                    self.lang_filter_combo.set("All Languages")
                except Exception as e:
                    print(f"WARN: Could not populate language filter: {e}")
            if hasattr(self, 'gender_filter_combo') and self.gender_filter_combo.winfo_exists():
                self.gender_filter_combo.configure(state=ctk.NORMAL)
                # FIX: set() on a disabled CTkComboBox is silently ignored, so the
                # initial "All" never stuck - set it now that it is enabled.
                try:
                    self.gender_filter_combo.set("All")
                except Exception:
                    pass
            if hasattr(self, 'fav_btn') and self.fav_btn.winfo_exists():
                self.fav_btn.configure(state=ctk.NORMAL)
            if hasattr(self, 'fav_only_checkbox') and self.fav_only_checkbox.winfo_exists():
                self.fav_only_checkbox.configure(state=ctk.NORMAL)
            self.update_fav_button()
            # Kayitli dil/cinsiyet/fav/secili sesi geri yukle (yoksa varsayilan filtre)
            try:
                self._apply_voice_related_settings()
            except Exception as e:
                print(f"WARN: Could not restore voice settings: {e}")
                try:
                    self._apply_voice_filters()
                except Exception:
                    pass
            try:
                self.refresh_profile_buttons()
            except Exception:
                pass
            self.update_status(f"Ready. {len(voice_list)} voices loaded.")
            # Determine final state based on whether audio is already loaded
            current_state = 'generated' if self.audio_file_path else 'idle'
            self.set_ui_state(current_state)
        else:
            # If the list is empty (error during load)
            self._set_voice_list_values(["No voices found"], state=ctk.DISABLED)
            if hasattr(self, 'voice_search_entry') and self.voice_search_entry.winfo_exists():
                self.voice_search_entry.configure(state=ctk.DISABLED)
            if hasattr(self, 'lang_filter_combo') and self.lang_filter_combo.winfo_exists():
                self.lang_filter_combo.configure(state=ctk.DISABLED)
            if hasattr(self, 'gender_filter_combo') and self.gender_filter_combo.winfo_exists():
                self.gender_filter_combo.configure(state=ctk.DISABLED)
            if hasattr(self, 'fav_btn') and self.fav_btn.winfo_exists():
                self.fav_btn.configure(state=ctk.DISABLED)
            if hasattr(self, 'fav_only_checkbox') and self.fav_only_checkbox.winfo_exists():
                self.fav_only_checkbox.configure(state=ctk.DISABLED)
            self.update_status("❌ Error: No voices could be loaded.")
            self.set_ui_state('error_no_voices') # Specific error state

    async def _generate_audio_task(self, text: str, voice_short_name: str, rate_str: str, pitch_str: str):
        """Coroutine to generate audio and save it to a temporary file."""
        tmp_path = None
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice_short_name, rate=rate_str, pitch=pitch_str)
            # Create a temporary file (it won't be deleted automatically with delete=False)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", prefix="edge_tts_") as tmp_file:
                tmp_path = tmp_file.name

            # Save the audio stream from edge-tts to the created temp file
            await communicate.save(tmp_path)

            # Verify that the file was created and is not empty
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                self.audio_file_path = tmp_path # Store the path if valid
                print(f"INFO: Audio saved to temp file: {self.audio_file_path}")
                # Schedule the _on_audio_generated call on the main thread
                self.after(0, self._on_audio_generated)
            else:
                 # File is missing or empty
                 print(f"ERROR: Temp audio file missing or empty after generation: {tmp_path}")
                 if tmp_path and os.path.exists(tmp_path): # Attempt to remove if it exists
                     try: os.remove(tmp_path)
                     except OSError as rm_err: print(f"WARN: Could not remove invalid temp file {tmp_path}: {rm_err}")
                 self.audio_file_path = None
                 self.after(0, self._on_generate_failed,
                            "❌ Error: Failed to generate valid audio file.")

        except edge_tts.exceptions.NoAudioGeneratedError as e:
             # Specific error from edge-tts if no audio is produced (e.g., empty text)
             print(f"ERROR: Edge TTS reported no audio generated: {e}")
             if tmp_path and os.path.exists(tmp_path): # Clean up temp file if created
                 try: os.remove(tmp_path)
                 except OSError as rm_err: print(f"WARN: Could not remove temp file {tmp_path}: {rm_err}")
             self.audio_file_path = None
             # Use get_input_text for the message check
             self.after(0, self._on_generate_failed,
                        "❌ Error: No audio generated (is input text empty?).")
        except Exception as e:
            # Catch any other exceptions during generation
            print(f"ERROR: Exception during audio generation: {e}")
            if tmp_path and os.path.exists(tmp_path): # Attempt cleanup
                 try: os.remove(tmp_path)
                 except OSError as rm_err: print(f"WARN: Could not remove temp file {tmp_path}: {rm_err}")
            self.audio_file_path = None
            self.after(0, self._on_generate_failed, f"❌ Error generating audio: {e}")

    def _on_audio_generated(self):
        """Callback on the main thread after the temporary audio file is created."""
        # Uretim bitti (basarili yukleme veya hata): bayragi indir ki
        # Generate yeniden acilabilsin ve gorunum kilitli kalmasin.
        self._generating = False
        print(f"INFO: Loading generated audio file: {self.audio_file_path}")
        if not self.just_playback_initialized or not self.player:
            self.update_status("❌ Error: Audio generated, but player is not ready."); self.set_ui_state('error_no_audio'); return
        if not self.audio_file_path or not os.path.exists(self.audio_file_path):
             self.update_status("❌ Error: Generated audio file path is invalid or missing."); self.set_ui_state('idle'); return

        try:
            # Stop the player if it's playing something else before loading the new file
            if self.player.playing or self.player.paused:
                self.player.stop()

            # Load the audio file into just_playback
            self.player.load_file(self.audio_file_path)
            # Add a small delay before getting duration, sometimes needed after load
            self.after(50, self._finish_audio_load)

        except Exception as e:
            # Catch errors during file loading *initiation* into just_playback
            print(f"ERROR: Failed to initiate loading audio file into player: {e}")
            self.update_status(f"❌ Error loading audio: {e}")
            self.audio_duration = 0
            self.set_ui_state('error_audio_format')
            self._delete_temp_audio_file() # Delete the problematic file


    def _finish_audio_load(self):
        """Gets duration and updates UI after just_playback has loaded the file."""
        self._generating = False # Emniyet: yukleme bitince bayrak inmis olmali
        if not self.just_playback_initialized or not self.player: return
        try:
            self.audio_duration = self.player.duration # Get duration from the player
            print(f"INFO: Audio file loaded. Duration: {self.audio_duration:.2f}s")
            # Apply current volume
            try:
                self.player.set_volume(self.current_volume)
            except Exception as ve:
                print(f"WARN: Could not apply volume after load: {ve}")

            # Check if the duration is valid
            if self.audio_duration > 0:
                self.update_status("✅ Audio generated! Press Play.")
                self.set_ui_state('generated') # State: ready to be played
                if hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists():
                    self.progress_slider.set(0) # Reset slider
                self.update_time_label(0, self.audio_duration) # Update time label
            else:
                # Invalid duration (0 or negative)
                print(f"WARN: Audio file loaded but reports invalid duration ({self.audio_duration:.2f}s). File might be corrupted.")
                self.update_status("❌ Error: Audio file seems invalid (0 duration).")
                self.set_ui_state('error_audio_format') # Specific error state
                self._delete_temp_audio_file() # Delete the invalid file

        except Exception as e:
             # Catch errors getting duration or updating UI
             print(f"ERROR: Failed to finalize audio load (get duration/update UI): {e}")
             self.update_status(f"❌ Error finalizing audio load: {e}")
             self.audio_duration = 0
             self.set_ui_state('error_audio_format')
             self._delete_temp_audio_file()


    # --- Audio Playback Controls ---
    def toggle_play_pause(self):
        """Starts, pauses, or resumes audio playback."""
        if not self.just_playback_initialized or not self.player:
            self.update_status("❌ Error: Audio player not ready."); return
        # Need a valid audio file and duration > 0 to play/pause
        if not self.audio_file_path or not os.path.exists(self.audio_file_path) or self.audio_duration <= 0:
            # Attempt reload if file exists but duration is invalid (maybe previous load failed)
            if self.audio_file_path and os.path.exists(self.audio_file_path):
                 print("WARN: Audio has invalid duration, attempting reload...")
                 self._on_audio_generated() # Try the loading process again
                 # Note: _on_audio_generated is async in effect due to _finish_audio_load,
                 # so we can't immediately check duration here. Assume it will work or fail later.
                 return # Exit, let the reload process handle the state
            else:
                 self.update_status("❌ Error: No valid audio loaded."); return

        try:
            if self.player.playing:
                self.player.pause()
                self._stop_progress_updater() # Stop updates when paused
                self.set_ui_state('paused'); self.update_status("⏸ Audio paused.")
            elif self.player.paused:
                self.player.resume()
                self.set_ui_state('playing'); self.update_status("▶ Resuming audio...")
                self._start_progress_updater() # Resume updates
            else: # If not playing/paused (i.e., stopped or initial state)
                # Ensure seeked to start if stopped previously? just_playback usually resumes
                # self.player.seek(0) # Optional: uncomment to always start from beginning after stop
                self.player.play() # Start from last position (or beginning if stopped/newly loaded)
                self.set_ui_state('playing'); self.update_status("▶ Playing audio...")
                self._start_progress_updater() # Start progress updates
        except Exception as e:
            print(f"ERROR: Exception during toggle_play_pause: {e}")
            self.update_status(f"❌ Playback Error: {e}")
            self.set_ui_state('generated') # Revert to generated state on error

    def stop_audio(self):
        """Stops audio playback and resets position to the beginning."""
        if not self.just_playback_initialized or not self.player: return

        # Only stop if currently playing or paused
        if self.player.playing or self.player.paused:
            try:
                self.player.stop()
                self._stop_progress_updater()
                # Reset UI to initial position
                if hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists():
                    self.progress_slider.set(0)
                self.update_time_label(0, self.audio_duration)
                self.set_ui_state('generated'); # State returns to 'ready to play'
                self.update_status("⏹ Audio stopped.")
            except Exception as e:
                print(f"ERROR: Exception during stop_audio: {e}")
                self.update_status(f"❌ Error stopping audio: {e}")
                self.set_ui_state('generated') # Still try to reset state
        else:
            # If already stopped, ensure UI is consistent
            self._stop_progress_updater()
            if hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists():
                 self.progress_slider.set(0)
            self.update_time_label(0, self.audio_duration)
            self.set_ui_state('generated')

    # --- Progress Update & Seeking Logic ---
    def _start_progress_updater(self):
        """Starts the loop for updating the progress bar and time label."""
        self._stop_progress_updater() # Ensure any previous updater is stopped
        # Only start if player is ready and duration is valid
        if self.player and self.audio_duration > 0:
             # Schedule the first call to _update_progress after a short delay
             self._after_id_update_progress = self.after(AUDIO_UPDATE_INTERVAL_MS, self._update_progress)

    def _stop_progress_updater(self):
        """Stops the progress update loop."""
        if self._after_id_update_progress:
            try:
                 self.after_cancel(self._after_id_update_progress)
            except ValueError: # Can happen if ID is invalid (e.g., already cancelled)
                 pass
            except Exception as e:
                 # Catch TclError if app is closing
                 if "application has been destroyed" not in str(e):
                      print(f"WARN: Error cancelling progress updater: {e}")
            self._after_id_update_progress = None


    def pause_updates_on_drag(self, event=None):
        """Called when the user presses the progress slider."""
        if self._can_seek(): # Only set flag if seeking is possible
            self._slider_being_dragged = True
            # Optional: Could also stop the updater here if needed
            # self._stop_progress_updater()

    def _update_progress(self):
        """Method called periodically to update the progress UI."""
        # Safety check: Stop if player is not ready or window closing
        if not self.just_playback_initialized or not self.player or not self.winfo_exists():
            self._stop_progress_updater(); return

        # Update only if playing AND the user is not dragging the slider
        if self.player.playing and not self._slider_being_dragged:
            try:
                current_pos_sec = self.player.curr_pos
                total_duration = self.audio_duration

                # Ensure duration is valid before calculating percentage
                if total_duration > 0:
                    # Calculate progress percentage (0-100)
                    progress_percent = min(100, max(0, (current_pos_sec / total_duration) * 100))

                    # Check if slider exists before updating
                    slider_exists = hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists()

                    # Update slider UI only if the value changed significantly (reduces flicker)
                    if slider_exists:
                        # Use try-except for slider access as it might be destroyed during close
                        try:
                             current_slider_val = self.progress_slider.get()
                             if abs(current_slider_val - progress_percent) > 0.5: # 0.5% tolerance
                                self.progress_slider.set(progress_percent)
                        except Exception as slider_e:
                              print(f"WARN: Error accessing slider during progress update: {slider_e}")
                              slider_exists = False # Assume slider is gone

                    # Update time label
                    self.update_time_label(current_pos_sec, total_duration)
                else:
                    # Abnormal condition if duration is 0 while playing, stop the updater
                    print("WARN: Invalid duration detected during progress update.")
                    self._stop_progress_updater()
                    if hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists():
                         try: self.progress_slider.set(0)
                         except: pass # Ignore errors if closing
                    self.update_time_label(0, 0)
                    return # Do not reschedule

                # Reschedule the next update call ONLY if still playing
                # Check player state *again* as it might have finished between checks
                if self.player.playing:
                   # Check window exists before scheduling next 'after'
                   if self.winfo_exists():
                        self._after_id_update_progress = self.after(AUDIO_UPDATE_INTERVAL_MS, self._update_progress)
                   else:
                        self._after_id_update_progress = None # Window closed
                else:
                    # Playback finished naturally (detected by state check)
                    print("INFO: Playback finished naturally.")
                    self._stop_progress_updater()
                    # Call stop_audio after a short delay to reset UI/state if window still exists
                    if self.winfo_exists():
                         self.after(50, self.stop_audio) # Increased delay slightly

            except Exception as e:
                # Catch errors during the update process
                # Check if error is due to closing window
                if "application has been destroyed" not in str(e) and "invalid command name" not in str(e):
                     print(f"ERROR: Exception in progress update loop: {e}")
                self._stop_progress_updater() # Stop updater on error

        # If not playing or being dragged, ensure updater is stopped
        elif (not self.player.playing or self._slider_being_dragged) and self._after_id_update_progress:
             self._stop_progress_updater()


    def format_time(self, seconds: float) -> str:
        """Formats seconds into an MM:SS string."""
        if not isinstance(seconds, (int, float)) or seconds < 0: seconds = 0
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    def update_time_label(self, current_seconds: float, total_seconds: float):
        """Updates the 'MM:SS / MM:SS' time label."""
        # Ensure non-negative inputs
        current_seconds = max(0, current_seconds if isinstance(current_seconds, (int, float)) else 0)
        total_seconds = max(0, total_seconds if isinstance(total_seconds, (int, float)) else 0)
        # Format times
        current_time_str = self.format_time(current_seconds)
        total_time_str = self.format_time(total_seconds)
        # Update the label if it exists
        if hasattr(self, 'time_label') and self.time_label.winfo_exists():
            try:
                 self.time_label.configure(text=f"{current_time_str} / {total_time_str}")
            except Exception as e:
                 # Ignore TclError if widget destroyed during update
                 if "application has been destroyed" not in str(e):
                      print(f"WARN: Error updating time label: {e}")

    def _can_seek(self) -> bool:
        """Checks if the current conditions allow seeking."""
        # Check player state too
        player_ready = self.just_playback_initialized and self.player and (self.player.playing or self.player.paused)
        return (player_ready
                and self.audio_file_path and os.path.exists(self.audio_file_path) # Check file path
                and self.audio_duration > 0.001)


    def _perform_seek(self, target_seek_time_sec: float):
        """Core logic for seeking: stop updater, seek, update UI, schedule updater restart."""
        if not self._can_seek(): return # Do nothing if seeking isn't possible

        try:
            was_playing = self.player.playing # Remember if it was playing before seek
            self._stop_progress_updater() # Stop updater first

            # Clamp target time to valid duration range (allow seeking very close to end)
            target_seek_time_sec = max(0, min(target_seek_time_sec, self.audio_duration - 0.01)) # Subtract tiny amount

            # Perform seek using just_playback
            self.player.seek(target_seek_time_sec)

            # Schedule immediate UI update after a short delay (allows seek to register)
            # Ensure window exists before scheduling 'after' calls
            if self.winfo_exists():
                 self.after(30, self._update_ui_after_seek_internal)
                 # Schedule the updater restart check only if it was playing before
                 if was_playing:
                     self.after(80, self._maybe_restart_updater) # Slightly longer delay
            else:
                 print("WARN: Window closed during seek operation, skipping UI updates.")

        except Exception as e:
             # Catch errors during the seek operation
             print(f"ERROR: Exception during seek operation: {e}")
             self.update_status(f"❌ Error seeking: {e}")
             # Still try to schedule updater restart check if it was playing
             if self.winfo_exists() and was_playing:
                 self.after(80, self._maybe_restart_updater)


    def seek_relative(self, seconds_to_add: int):
        """Jumps forward or backward by a specified number of seconds."""
        if not self._can_seek(): return
        current_pos_sec = self.player.curr_pos
        target_seek_time_sec = current_pos_sec + seconds_to_add
        self._perform_seek(target_seek_time_sec) # Use the helper

    def seek_audio_on_release(self, event=None):
        """Called when the user releases the click on the progress slider."""
        if not self._can_seek(): # Check if seeking is possible *before* clearing the flag
             self._slider_being_dragged = False # Always clear flag on release
             return
        if not hasattr(self, 'progress_slider'):
             self._slider_being_dragged = False
             return

        seek_percent = self.progress_slider.get()
        # Clear the flag *before* performing the seek
        self._slider_being_dragged = False
        # Calculate target time based on slider percentage
        target_seek_time_sec = (seek_percent / 100.0) * self.audio_duration
        self._perform_seek(target_seek_time_sec) # Use the helper

    def _update_ui_after_seek_internal(self):
        """Updates the slider position and time label immediately after a seek."""
        if not self.just_playback_initialized or not self.player or not self.winfo_exists(): return
        # Update only if player is in a valid state (playing/paused)
        if self.player.playing or self.player.paused:
            try:
                 current_pos = self.player.curr_pos
                 duration = self.audio_duration
                 self.update_time_label(current_pos, duration) # Update time label
                 if duration > 0 and hasattr(self, 'progress_slider') and self.progress_slider.winfo_exists():
                      # Update slider position
                      percent = min(100, max(0, (current_pos / duration) * 100))
                      self.progress_slider.set(percent)
            except Exception as e:
                 # Catch errors during post-seek UI update
                 if "application has been destroyed" not in str(e):
                      print(f"ERROR: Exception updating UI after seek: {e}")

    def _maybe_restart_updater(self):
        """Checks conditions and restarts the progress updater if necessary."""
        if not self.just_playback_initialized or not self.player or not self.winfo_exists(): return
        # Restart only if: playing, NOT dragging, AND updater is not already running
        if self.player.playing and not self._slider_being_dragged and not self._after_id_update_progress:
             self._start_progress_updater()

    # --- File Operations (Load Text, Save Audio) ---
    def load_text_from_file(self):
        """Opens a dialog to select a text (.txt or .srt) file and loads its content."""
        file_path = filedialog.askopenfilename(
            title="Select Text or Subtitle File", # Dialog title
            filetypes=[("Text files", "*.txt"), ("SubRip Subtitles", "*.srt"), ("All files", "*.*")] # File type filters
        )
        if not file_path:
            self.update_status("File selection cancelled."); return # User cancelled

        try:
            content = ""
            filename = os.path.basename(file_path)
            print(f"INFO: Loading file content from: {file_path}")

            if filename.lower().endswith(".srt"):
                # Parse SRT file
                content = self._parse_srt(file_path)
                status_msg = f"✅ Loaded dialogue from {filename}" if content else f"⚠️ No dialogue found in SRT: {filename}"
            else:
                # Read plain text file
                try:
                    # Try UTF-8 encoding first (more common)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    # If UTF-8 fails, try default system encoding (less reliable)
                    print(f"WARN: UTF-8 decoding failed for {filename}. Trying default system encoding.")
                    try:
                        # Python 3: encoding=None uses default locale encoding
                        with open(file_path, 'r', encoding=None) as f:
                            content = f.read()
                    except UnicodeDecodeError as e_enc:
                        # Handle specific encoding errors on second attempt
                        print(f"ERROR: Failed to read {filename} with default encoding: {e_enc}")
                        self.update_status(f"❌ Error reading file (encoding issue)"); return
                    except Exception as e_read_alt:
                         print(f"ERROR: Failed to read {filename} with default encoding: {e_read_alt}")
                         self.update_status(f"❌ Error reading file"); return
                except Exception as e_read_main:
                     # Catch other file reading errors (e.g., permission)
                     print(f"ERROR: Failed to read file {filename}: {e_read_main}")
                     self.update_status(f"❌ Error reading file"); return
                status_msg = f"✅ Loaded text from {filename}"

            # Insert content into the textbox
            if hasattr(self, 'textbox') and self.textbox.winfo_exists():
                 was_disabled = self._textbox_writable()
                 self.textbox_placeholder_active = False # Ensure placeholder is off
                 self.textbox_default_sample_active = False # Sample replaced by file
                 self.textbox.delete("1.0", ctk.END) # Clear old text
                 if self.default_textbox_color: # Ensure we have a valid color
                     self.textbox.configure(text_color=self.default_textbox_color) # Set normal color
                 if content:
                     self.textbox.insert("1.0", content) # Insert new text
                 # Check immediately if the loaded content was empty, and reset placeholder if so
                 self._check_and_set_placeholder()
                 self.update_char_counter()
                 self._update_ui_after_text_change()
                 self._textbox_restore(was_disabled)
            self.update_status(status_msg) # Update status bar
            self.set_ui_state('idle') # Update button states based on new text content

        except FileNotFoundError:
            print(f"ERROR: File not found: {file_path}")
            self.update_status("❌ Error: File not found.")
        except Exception as e:
            # Catch other errors during loading/parsing
            print(f"ERROR: Exception loading/processing file {file_path}: {e}")
            self.update_status(f"❌ Error loading file.")


    def _parse_srt(self, file_path: str) -> str:
        """Reads an SRT file and extracts only the dialogue text."""
        dialogue_lines: list[str] = []
        full_content: str = ""
        try:
            # Try reading as UTF-8 with error handling
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                full_content = f.read()
        except Exception as e: # Catch potential errors even opening the file
            print(f"ERROR: Failed to read SRT file {file_path}: {e}")
            return ""

        lines = full_content.splitlines()
        buffer: list[str] = []
        is_dialogue_block: bool = False
        # Regex patterns for line detection (more robust)
        block_number_pattern = re.compile(r'^\d+\s*$') # Matches lines with only numbers
        timestamp_pattern = re.compile(r'^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{3}.*') # Allow comma or period for ms

        for line in lines:
            line = line.strip()
            if not line:
                 # Empty line, potential end of block
                 if buffer:
                     dialogue_lines.append(" ".join(buffer))
                     buffer = []
                 is_dialogue_block = False # Reset state
            elif block_number_pattern.match(line) and not is_dialogue_block:
                 # Block number line (ignore if already in dialogue or buffer has text)
                 # Reset just in case of malformed SRT
                 if buffer:
                     dialogue_lines.append(" ".join(buffer))
                     buffer = []
                 is_dialogue_block = False
            elif timestamp_pattern.match(line):
                 # Timestamp line, definitely start of a new dialogue block
                 if buffer: # Append previous buffer if any
                     dialogue_lines.append(" ".join(buffer))
                 buffer = [] # Clear buffer for new dialogue
                 is_dialogue_block = True
            elif is_dialogue_block:
                 # Dialogue text line, clean simple tags and add to buffer
                 cleaned_line = re.sub(r'<[^>]+>', '', line) # Remove simple HTML/XML tags
                 cleaned_line = re.sub(r'{[^}]+}', '', cleaned_line) # Remove simple curly brace tags (e.g., {\an8})
                 # More aggressive cleaning (optional): remove lines starting with typical non-dialogue chars
                 # if not cleaned_line.startswith(('-', '[', '(')):
                 if cleaned_line: # Only add if not empty after cleaning
                     buffer.append(cleaned_line)
            # else: Ignore lines that don't fit patterns (comments, etc.)


        # Add the last buffer if file doesn't end with an empty line
        if buffer: dialogue_lines.append(" ".join(buffer))

        # Join all collected dialogue lines into a single string with spaces
        final_text = " ".join(line for line in dialogue_lines if line)
        # Further cleanup: Replace multiple spaces with single space
        final_text = re.sub(r'\s{2,}', ' ', final_text).strip()
        return final_text


    def save_audio(self):
        """Opens a dialog to save the temporary audio file to a user-chosen location."""
        if not self.audio_file_path or not os.path.exists(self.audio_file_path):
             self.update_status("❌ No generated audio file to save."); return
        # Ensure playback is stopped before saving
        if self.just_playback_initialized and self.player and (self.player.playing or self.player.paused):
            self.update_status("⚠️ Please stop playback before saving."); return

        try: # Create default filename from the beginning of the text
            # Use get_input_text to avoid using placeholder as filename basis
            initial_text = self.get_input_text()[:40].strip().replace("\n", " ") # Limit length
            # Sanitize filename: allow alphanumeric, space, underscore, hyphen
            sanitized_text = "".join(c for c in initial_text if c.isalnum() or c in (' ', '_', '-')).rstrip()
            # Replace spaces with underscores for better compatibility
            sanitized_text = sanitized_text.replace(' ', '_')
            # Limit length again after sanitization
            initial_filename = f"{sanitized_text[:30] if sanitized_text else 'speech'}.mp3"
        except Exception as e:
            print(f"WARN: Error generating initial filename: {e}")
            initial_filename = "speech.mp3" # Fallback name

        # Open 'Save As' dialog
        file_path = filedialog.asksaveasfilename(
            defaultextension=".mp3",
            filetypes=[("MP3 audio file", "*.mp3"), ("All files", "*.*")], # File type options
            title="Save Audio As...", # Dialog title
            initialfile=initial_filename # Default filename suggestion
        )

        if file_path: # If the user selected a path and name
            try:
                print(f"INFO: Copying temp file {self.audio_file_path} to {file_path}")
                # Copy the temporary file to the chosen destination (binary mode)
                with open(self.audio_file_path, 'rb') as src, open(file_path, 'wb') as dst:
                    # Read and write in chunks for potentially large files
                    while True:
                        chunk = src.read(8192) # Read 8KB at a time
                        if not chunk: break # End of file
                        dst.write(chunk)
                self.update_status(f"✅ Audio saved successfully to {os.path.basename(file_path)}")
            except IOError as e:
                print(f"ERROR: IOError during file save: {e}")
                self.update_status(f"❌ Error saving file: {e}")
            except Exception as e:
                print(f"ERROR: Unexpected exception during file save: {e}")
                self.update_status(f"❌ An unexpected error occurred during saving: {e}")
        else:
            # User cancelled the save dialog
            self.update_status("Save operation cancelled.")

    # --- Cleanup ---
    def _delete_temp_audio_file(self):
        """Deletes the temporary audio file if it exists, with retries."""
        path_to_delete = self.audio_file_path
        if path_to_delete and os.path.exists(path_to_delete):
            print(f"INFO: Preparing to delete temp file: {path_to_delete}")
            # Clear internal references *before* attempting deletion
            self.audio_file_path = None
            self.audio_duration = 0

            # Stop the player if it's active and loaded this file
            if self.just_playback_initialized and self.player and (self.player.playing or self.player.paused):
                 # Check if the player's source matches the file to delete
                 # Note: just_playback doesn't directly expose the loaded file path easily.
                 # We assume if a file exists, the player *might* be using it.
                 print(f"INFO: Stopping player before attempting to delete potential source file.")
                 try:
                     self.player.stop()
                     time.sleep(0.15) # Give OS time to release handle
                 except Exception as e:
                     # Ignore errors if player is already stopped or invalid
                     if "Playback has not been initialized" not in str(e):
                          print(f"WARN: Exception while stopping player before delete: {e}")

            # Attempt to delete the physical file with retries
            max_retries = 4
            retry_delay = 0.25 # seconds
            for attempt in range(max_retries):
                try:
                    os.remove(path_to_delete)
                    print(f"INFO: Deleted temp file: {path_to_delete}")
                    path_to_delete = None # Mark as deleted successfully
                    break # Exit loop if successful
                except PermissionError as e:
                    print(f"WARN: Attempt {attempt + 1}/{max_retries} - PermissionError removing temp file {path_to_delete}: {e}. Retrying...")
                    time.sleep(retry_delay)
                except OSError as e:
                    print(f"ERROR: Attempt {attempt + 1}/{max_retries} - OSError removing temp file {path_to_delete}: {e}. Retrying...")
                    time.sleep(retry_delay)
                except Exception as e:
                    print(f"ERROR: Unexpected exception deleting temp file {path_to_delete} (Attempt {attempt + 1}): {e}")
                    break # Don't retry on unexpected errors

            if path_to_delete and os.path.exists(path_to_delete):
                print(f"ERROR: Failed to delete temp file after {max_retries} retries: {path_to_delete}")
                # Log the failure, internal path is already None.
        else:
            # Path was already None or file didn't exist
            if self.audio_file_path: # Clear internal refs just in case
                self.audio_file_path = None
                self.audio_duration = 0


    def on_closing(self):
        """Called when the application window is closed."""
        print("INFO: Closing application...")
        # Debounce'lu kayitlari iptal edip son durumu hemen yaz
        # (rate/pitch/favori/secili ses kaybolmasin)
        try:
            if getattr(self, '_settings_after_id', None):
                try:
                    self.after_cancel(self._settings_after_id)
                except Exception:
                    pass
                self._settings_after_id = None
        except Exception:
            pass
        try:
            if getattr(self, '_search_after_id', None):
                try:
                    self.after_cancel(self._search_after_id)
                except Exception:
                    pass
                self._search_after_id = None
        except Exception:
            pass
        try:
            if hasattr(self, 'lang_filter_combo') and hasattr(self.lang_filter_combo, 'close_popup'):
                self.lang_filter_combo.close_popup()
        except Exception:
            pass
        try:
            self.save_settings()
        except Exception as e:
            print(f"WARN: Could not save settings on close: {e}")
        try:
            self.save_favorites()
        except Exception as e:
            print(f"WARN: Could not save favorites on close: {e}")
        self._stop_progress_updater() # Stop the UI update loop

        # Stop the player if active
        if self.just_playback_initialized and self.player:
             try:
                  if self.player.playing or self.player.paused:
                       print("INFO: Stopping audio playback...")
                       self.player.stop()
                       time.sleep(0.1) # Short pause after stopping
             except Exception as e:
                  # Ignore "Playback has not been initialized" error if player failed
                  if "Playback has not been initialized" not in str(e):
                     print(f"WARN: Exception stopping player during close: {e}")

        # Delete the last temporary file
        print("INFO: Cleaning up temporary audio file...")
        self._delete_temp_audio_file()

        # I Removed the problematic after_cancel loop entirely
        # The _stop_progress_updater() call above already handles the main updater
        # Got exceptions during the after_cancel call otherwise.

        self.destroy() # Close the Tkinter window


# --- Execution Entry Point ---
if __name__ == "__main__":
    # Check if just_playback is available before starting the main GUI
    if not JUST_PLAYBACK_AVAILABLE:
        # Display a simple error window if the library is missing
        error_root = ctk.CTk()
        # Set mode for the error window too
        ctk.set_appearance_mode("System")
        error_root.title("Dependency Error")
        error_root.geometry("450x120")
        error_label = ctk.CTkLabel(
            error_root,
            text="Required library 'just_playback' is missing or failed to load.\n"
                 "Please install it using:\n"
                 "pip install just_playback",
            font=ctk.CTkFont(size=13)
        )
        error_label.pack(pady=20, padx=20)
        # Automatically close the error window after a few seconds
        error_root.after(7000, error_root.destroy)
        error_root.mainloop()
    else:
        # If the library is available, run the main application
        app = EdgeTTSApp()
        # Set the close window action to call our on_closing method
        app.protocol("WM_DELETE_WINDOW", app.on_closing)
        app.mainloop()

# --- END OF FILE final.py ---