import json, os, sys, threading, time, queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import cv2
    import numpy as np
    import mss
    import pyautogui
except Exception as e:
    cv2 = np = mss = pyautogui = None
    IMPORT_ERROR = str(e)
else:
    IMPORT_ERROR = None

APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CFG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "session.csv")

DEFAULTS = {
    "screen": {"left": 0, "top": 0, "width": 1920, "height": 1080},
    "marker_hsv_low": [0, 100, 80],
    "marker_hsv_high": [10, 255, 255],
    "marker_hsv_low2": [170, 100, 80],
    "marker_hsv_high2": [180, 255, 255],
    "marker_min_area": 100,
    "close_area": 2500,
    "horizontal_deadzone": 45,
    "steer_gain": 0.0025,
    "approach_key": "w",
    "interact_key": "e",
    "left_key": "a",
    "right_key": "d",
    "minigame": {
        "enabled": True,
        "roi": [600, 350, 720, 260],
        "green_low": [35, 70, 70],
        "green_high": [90, 255, 255],
        "white_low": [0, 0, 210],
        "white_high": [180, 55, 255],
        "action_key": "e",
        "timeout_seconds": 15
    },
    "pay_per_box": 11.0,
    "interaction_cooldown": 2.0,
    "pickup_wait": 2.5,
    "delivery_wait": 2.0,
    "max_run_minutes": 120
}

def load_config():
    if not os.path.exists(CFG_PATH):
        save_config(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # retain defaults for newly introduced fields
    def merge(dst, src):
        for k, v in src.items():
            if k not in dst: dst[k] = v
            elif isinstance(v, dict) and isinstance(dst[k], dict): merge(dst[k], v)
    merge(data, DEFAULTS)
    return data

def save_config(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

class PortBot:
    """Configurable screen-vision test agent. Requires calibration for the target test server."""
    def __init__(self, emit):
        self.emit = emit
        self.stop_event = threading.Event()
        self.thread = None
        self.running = False
        self.boxes = 0
        self.errors = 0
        self.started = None
        self.cfg = load_config()
        self._pressed = set()

    def say(self, msg):
        self.emit(("log", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"))

    def press(self, key):
        if pyautogui and key not in self._pressed:
            pyautogui.keyDown(key)
            self._pressed.add(key)

    def release_all(self):
        if pyautogui:
            for k in list(self._pressed):
                try: pyautogui.keyUp(k)
                except Exception: pass
        self._pressed.clear()

    def tap(self, key, duration=0.08):
        self.release_all()
        pyautogui.press(key)
        time.sleep(duration)

    def screenshot(self):
        s = self.cfg["screen"]
        with mss.mss() as cap:
            shot = cap.grab({"left": int(s["left"]), "top": int(s["top"]),
                             "width": int(s["width"]), "height": int(s["height"])})
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    def find_marker(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        c = self.cfg
        m1 = cv2.inRange(hsv, np.array(c["marker_hsv_low"]), np.array(c["marker_hsv_high"]))
        m2 = cv2.inRange(hsv, np.array(c["marker_hsv_low2"]), np.array(c["marker_hsv_high2"]))
        mask = cv2.bitwise_or(m1, m2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = [x for x in contours if cv2.contourArea(x) >= float(c["marker_min_area"])]
        if not candidates: return None
        cnt = max(candidates, key=cv2.contourArea)
        area = cv2.contourArea(cnt)
        M = cv2.moments(cnt)
        if M["m00"] == 0: return None
        x = int(M["m10"] / M["m00"])
        y = int(M["m01"] / M["m00"])
        return x, y, area

    def approach_marker(self, marker):
        x, y, area = marker
        w = int(self.cfg["screen"]["width"])
        dx = x - w // 2
        dead = int(self.cfg["horizontal_deadzone"])
        self.release_all()
        if dx < -dead:
            self.press(self.cfg["left_key"])
            self.press(self.cfg["approach_key"])
        elif dx > dead:
            self.press(self.cfg["right_key"])
            self.press(self.cfg["approach_key"])
        else:
            self.press(self.cfg["approach_key"])
        return area >= float(self.cfg["close_area"])

    def play_minigame(self):
        mg = self.cfg["minigame"]
        if not mg["enabled"]:
            self.tap(self.cfg["interact_key"])
            return True
        started = time.time()
        x0, y0, ww, hh = [int(v) for v in mg["roi"]]
        while not self.stop_event.is_set() and time.time() - started < float(mg["timeout_seconds"]):
            frame = self.screenshot()
            roi = frame[y0:y0+hh, x0:x0+ww]
            if roi.size == 0:
                self.say("Ошибка ROI мини-игры: проверьте config.json")
                return False
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            green = cv2.inRange(hsv, np.array(mg["green_low"]), np.array(mg["green_high"]))
            white = cv2.inRange(hsv, np.array(mg["white_low"]), np.array(mg["white_high"]))
            gy, gx = np.where(green > 0)
            wy, wx = np.where(white > 0)
            if len(gx) and len(wx):
                slider_x = float(np.median(gx))
                target_min, target_max = float(np.percentile(wx, 5)), float(np.percentile(wx, 95))
                if target_min <= slider_x <= target_max:
                    self.release_all()
                    self.tap(mg["action_key"])
                    time.sleep(0.35)
                    return True
            time.sleep(0.025)
        return False

    def run_loop(self):
        self.running = True
        self.started = time.time()
        self.say("Тестовый агент запущен. Наблюдение за первой меткой.")
        state = "FIND_PICKUP"
        state_since = time.time()
        last_interact = 0
        try:
            while not self.stop_event.is_set():
                if time.time() - self.started > float(self.cfg["max_run_minutes"]) * 60:
                    self.say("Достигнут лимит времени теста.")
                    break
                frame = self.screenshot()
                marker = self.find_marker(frame)

                if state in ("FIND_PICKUP", "FIND_DELIVERY"):
                    if marker is None:
                        self.release_all()
                        time.sleep(0.15)
                        continue
                    close = self.approach_marker(marker)
                    if close and time.time() - last_interact > float(self.cfg["interaction_cooldown"]):
                        self.release_all()
                        self.tap(self.cfg["interact_key"])
                        last_interact = time.time()
                        if state == "FIND_PICKUP":
                            state = "WAIT_PICKUP"
                            state_since = time.time()
                            self.say("Метка подбора достигнута; ожидание анимации.")
                        else:
                            state = "MINIGAME"
                            state_since = time.time()
                            self.say("Точка доставки достигнута; запуск распознавания мини-игры.")
                elif state == "WAIT_PICKUP":
                    self.release_all()
                    if time.time() - state_since >= float(self.cfg["pickup_wait"]):
                        state = "FIND_DELIVERY"
                        self.say("Переход к поиску точки доставки.")
                elif state == "MINIGAME":
                    self.release_all()
                    ok = self.play_minigame()
                    if ok:
                        self.boxes += 1
                        earned = self.boxes * float(self.cfg["pay_per_box"])
                        self.emit(("stats", self.boxes, earned, self.errors, self.started))
                        self.say(f"Успешный тестовый цикл. Коробок: {self.boxes}; расчётная сумма: ${earned:.2f}")
                        state = "WAIT_DELIVERY"
                        state_since = time.time()
                    else:
                        self.errors += 1
                        self.emit(("stats", self.boxes, self.boxes * float(self.cfg["pay_per_box"]), self.errors, self.started))
                        self.say("Мини-игра не подтверждена или истёк тайм-аут; цикл остановлен для безопасности.")
                        break
                elif state == "WAIT_DELIVERY":
                    self.release_all()
                    if time.time() - state_since >= float(self.cfg["delivery_wait"]):
                        state = "FIND_PICKUP"
                        self.say("Цикл завершён; поиск следующей коробки.")
                time.sleep(0.03)
        except Exception as e:
            self.errors += 1
            self.say(f"Аварийная ошибка: {e}")
        finally:
            self.release_all()
            self.running = False
            self.emit(("stats", self.boxes, self.boxes * float(self.cfg["pay_per_box"]), self.errors, self.started))
            self.emit(("state", "Остановлен"))
            self.say("Агент остановлен; все клавиши отпущены.")

    def start(self):
        if self.running: return
        self.stop_event.clear()
        self.cfg = load_config()
        self.boxes = 0
        self.errors = 0
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.release_all()

class App:
    def __init__(self, root):
        self.root = root
        root.title("PORT BOT — тестовая среда")
        root.geometry("720x560")
        self.events = queue.Queue()
        self.bot = PortBot(self.events.put)
        self.start_time = None
        self.build()
        self.poll()

    def build(self):
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="PORT BOT — TEST BUILD", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(main, text="Для собственной тестовой среды. Требуется калибровка config.json.",
                  foreground="#666").pack(anchor="w", pady=(2,12))
        stats = ttk.Frame(main)
        stats.pack(fill="x")
        self.vars = {k: tk.StringVar(value=v) for k,v in {
            "state":"Остановлен", "boxes":"0", "earned":"$0.00", "errors":"0", "rate":"$0.00/ч"
        }.items()}
        for label, key in [("Состояние","state"),("Коробки","boxes"),("Начислено*","earned"),
                           ("Ошибки","errors"),("Темп*","rate")]:
            card = ttk.LabelFrame(stats, text=label, padding=10)
            card.pack(side="left", fill="both", expand=True, padx=3)
            ttk.Label(card, textvariable=self.vars[key], font=("Segoe UI", 14, "bold")).pack()
        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=12)
        ttk.Button(controls, text="Старт теста", command=self.start).pack(side="left")
        ttk.Button(controls, text="СТОП", command=self.stop).pack(side="left", padx=8)
        ttk.Button(controls, text="Открыть config.json", command=self.open_config).pack(side="left")
        ttk.Button(controls, text="Сохранить настройки", command=self.save).pack(side="left", padx=8)
        ttk.Label(main, text="Журнал событий").pack(anchor="w")
        self.log = tk.Text(main, height=18, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        ttk.Label(main, text="* Сумма расчётная: укажите фактическую выплату за коробку в config.json.",
                  foreground="#666").pack(anchor="w", pady=(8,0))

    def append(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        if IMPORT_ERROR:
            messagebox.showerror("Не установлены библиотеки", IMPORT_ERROR + "\n\nЗапустите install.bat.")
            return
        if not messagebox.askyesno("Подтверждение", "Запустить агент в собственной тестовой среде? Убедитесь, что игра открыта в оконном режиме и config.json откалиброван."):
            return
        self.vars["state"].set("Запуск…")
        self.bot.start()

    def stop(self):
        self.bot.stop()
        self.vars["state"].set("Остановка…")

    def open_config(self):
        os.startfile(CFG_PATH)

    def save(self):
        try:
            save_config(self.bot.cfg)
            messagebox.showinfo("Настройки", "Текущие настройки сохранены.")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def poll(self):
        try:
            while True:
                item = self.events.get_nowait()
                if item[0] == "log": self.append(item[1])
                elif item[0] == "state": self.vars["state"].set(item[1])
                elif item[0] == "stats":
                    _, boxes, earned, errors, started = item
                    self.vars["boxes"].set(str(boxes))
                    self.vars["earned"].set(f"${earned:.2f}")
                    self.vars["errors"].set(str(errors))
                    elapsed = max(1, time.time() - (started or time.time()))
                    self.vars["rate"].set(f"${earned * 3600 / elapsed:.2f}/ч")
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

if __name__ == "__main__":
    if IMPORT_ERROR:
        print("Missing dependencies:", IMPORT_ERROR)
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop(), root.destroy()))
    root.mainloop()
