"""
=============================================================================
 NINTENDO SWITCH REMOTE PLAY - ALL-IN-ONE CLIENT FOR PC
=============================================================================
Integração total em um único clique:
 - Stream de Vídeo e Áudio de baixa latência (SysDVR TCP Bridge ou RTSP via MPV / FFplay)
- Controle Remoto via Gamepad do PC (DualSense, Xbox, Switch Pro, 8BitDo)
- Envio direto de inputs em tempo real para o sys-botbase (porta 6000)
=============================================================================
"""

import sys
import os
import time
import json
import ipaddress
import socket
import select
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

# Permite leitura do gamepad mesmo quando a janela do MPV está focada em tela cheia
os.environ['SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS'] = '1'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

# Auto-instalação da dependência pygame caso não esteja presente no ambiente
try:
    import pygame
except ModuleNotFoundError:
    print("[Nintendo Switch Remote Play] Biblioteca 'pygame' não encontrada neste computador.")
    print("Instalando 'pygame' automaticamente via pip... Aguarde alguns instantes...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pygame"])
        import pygame
        print("[Nintendo Switch Remote Play] 'pygame' instalado com sucesso!")
    except Exception as e:
        print(f"Erro ao instalar 'pygame' automaticamente: {e}")
        print("Por favor, execute manualmente no terminal: pip install pygame")
        sys.exit(1)

import shutil
if getattr(sys, "frozen", False):
    # Keep editable config and logs beside the packaged executable.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
TOOLS_DIR = os.path.abspath(os.path.join(APP_DIR, "..", "tools"))
MPV_PATH = os.path.join(TOOLS_DIR, "mpv", "mpv.exe")
if not os.path.exists(MPV_PATH):
    sys_mpv = shutil.which("mpv")
    if sys_mpv:
        MPV_PATH = sys_mpv

FFPLAY_PATH = os.path.join(TOOLS_DIR, "ffmpeg", "bin", "ffplay.exe")
if not os.path.exists(FFPLAY_PATH):
    FFPLAY_PATH = r"C:\ffmpeg\bin\ffplay.exe"
if not os.path.exists(FFPLAY_PATH):
    sys_ffplay = shutil.which("ffplay")
    if sys_ffplay:
        FFPLAY_PATH = sys_ffplay

SYSDVR_CLIENT_PATH = os.path.join(TOOLS_DIR, "SysDVR-Client", "SysDVR-Client.exe")
PLAYER_LOG_FILE = os.path.join(APP_DIR, "player_debug.log")
APP_LOG_FILE = os.path.join(APP_DIR, "remote_play.log")

# Layouts de botões para conversão de controles de PC -> Nintendo Switch
# Padrão SDL2 Gamepad (0:A, 1:B, 2:X, 3:Y, 4:Back, 5:Guide, 6:Start, 7:LStick, 8:RStick, 9:LB, 10:RB, 11:DUp, 12:DDown, 13:DLeft, 14:DRight)
LAYOUT_NINTENDO = {
    0: "B",       # Botão inferior do controle (Xbox A / PS Cross) -> Switch B
    1: "A",       # Botão direito do controle (Xbox B / PS Circle) -> Switch A
    2: "Y",       # Botão esquerdo do controle (Xbox X / PS Square) -> Switch Y
    3: "X",       # Botão superior do controle (Xbox Y / PS Triangle) -> Switch X
    4: "MINUS",   # Select / Back / Share
    5: "HOME",    # Botão Guia / PS / Xbox
    6: "PLUS",    # Start / Options / Menu
    7: "LSTICK",  # Clique do analógico esquerdo
    8: "RSTICK",  # Clique do analógico direito
    9: "L",       # LB / L1
    10: "R",      # RB / R1
    11: "DUP",
    12: "DDOWN",
    13: "DLEFT",
    14: "DRIGHT",
}

LAYOUT_XBOX_STYLE = {
    0: "A",       # Botão inferior do controle -> Switch A (confirma com Xbox A)
    1: "B",       # Botão direito do controle -> Switch B (volta com Xbox B)
    2: "X",       # Botão esquerdo do controle -> Switch X
    3: "Y",       # Botão superior do controle -> Switch Y
    4: "MINUS",
    5: "HOME",
    6: "PLUS",
    7: "LSTICK",
    8: "RSTICK",
    9: "L",
    10: "R",
    11: "DUP",
    12: "DDOWN",
    13: "DLEFT",
    14: "DRIGHT",
}

class RemotePlayApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nintendo Switch Remote Play - All-in-One")
        self.root.geometry("640x660")
        self.root.resizable(False, False)
        self.root.configure(bg="#18181c")

        # Estado da conexão
        self.is_running = False
        self.botbase_socket = None
        self.video_process = None
        self.input_thread = None
        self.gamepad = None
        self.cmd_queue = queue.Queue()
        self._log_lock = threading.Lock()
        self._player_session = 0
        self._closing = False

        # Carrega configuração salva
        self.config = self.load_config()

        # Inicializa subsistema de controle
        pygame.init()
        pygame.joystick.init()

        self.setup_ui()
        self.start_gamepad_monitor()

    def load_config(self):
        default = {
            "switch_ip": "192.168.1.100",
            "layout": "nintendo", # "nintendo" ou "xbox"
            "deadzone": 0.08,
            "transport": "tcp",   # "tcp" ou "udp"
            "player": "sysdvr",   # SysDVR TCP Bridge é o player padrão desta versão
            "fullscreen": False
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        default.update(data)
            except Exception as e:
                self.log_message(f"Configuração inválida; usando valores padrão: {e}")
        if not isinstance(default.get("switch_ip"), str):
            default["switch_ip"] = "192.168.1.100"
        if default.get("transport") not in ("tcp", "udp"):
            default["transport"] = "tcp"
        if default.get("player") not in ("mpv", "ffplay", "sysdvr"):
            default["player"] = "mpv"
        if default.get("layout") not in ("nintendo", "xbox"):
            default["layout"] = "nintendo"
        if not isinstance(default.get("fullscreen"), bool):
            default["fullscreen"] = False
        try:
            deadzone = float(default.get("deadzone", 0.08))
            default["deadzone"] = deadzone if 0.0 <= deadzone <= 0.5 else 0.08
        except (TypeError, ValueError):
            default["deadzone"] = 0.08
        return default

    def save_config(self):
        self.config["switch_ip"] = self.ip_entry.get().strip()
        self.config["layout"] = self.layout_var.get()
        self.config["transport"] = self.transport_var.get()
        self.config["player"] = self.player_var.get()
        self.config["fullscreen"] = self.fullscreen_var.get()
        try:
            temp_file = CONFIG_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, CONFIG_FILE)
        except Exception as e:
            self.log_message(f"Erro ao salvar config.json: {e}")

    def log_message(self, message):
        try:
            with self._log_lock:
                with open(APP_LOG_FILE, "a", encoding="utf-8") as log_file:
                    log_file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    @staticmethod
    def probe_tcp_port(ip, port, timeout=1.5):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    def validate_switch_ip(self):
        raw_ip = self.ip_entry.get().strip()
        try:
            return str(ipaddress.IPv4Address(raw_ip))
        except ipaddress.AddressValueError:
            messagebox.showwarning("IP inválido", "Digite um endereço IPv4 válido para o Switch.")
            return None

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Header Banner
        header = tk.Frame(self.root, bg="#202026", height=70)
        header.pack(fill="x")
        
        lbl_title = tk.Label(header, text="🎮 NINTENDO SWITCH REMOTE PLAY", font=("Segoe UI", 16, "bold"), fg="#ffffff", bg="#202026")
        lbl_title.pack(pady=(12, 2))
        
        lbl_sub = tk.Label(header, text="Stream de Áudio/Vídeo + Controle sem fio unificados", font=("Segoe UI", 9), fg="#00d26a", bg="#202026")
        lbl_sub.pack(pady=(0, 10))

        # Container Principal
        main = tk.Frame(self.root, bg="#18181c", padx=25, pady=15)
        main.pack(fill="both", expand=True)

        # 1. Card: Conexão com o Switch
        card_conn = tk.LabelFrame(main, text=" 1. Conexão do Console ", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#202026", bd=1, relief="solid", padx=15, pady=10)
        card_conn.pack(fill="x", pady=(0, 12))

        tk.Label(card_conn, text="Endereço IP do Switch:", font=("Segoe UI", 9, "bold"), fg="#cccccc", bg="#202026").grid(row=0, column=0, sticky="w", pady=4)
        
        self.ip_entry = tk.Entry(card_conn, font=("Segoe UI", 11), bg="#141418", fg="#ffffff", insertbackground="#00d26a", bd=1, relief="solid", width=18)
        self.ip_entry.insert(0, self.config.get("switch_ip", "192.168.1.100"))
        self.ip_entry.grid(row=0, column=1, padx=10, pady=4)

        btn_test = tk.Button(card_conn, text="Testar Conexão", font=("Segoe UI", 8, "bold"), bg="#30303a", fg="#ffffff", activebackground="#40404f", activeforeground="#ffffff", bd=0, padx=10, pady=4, cursor="hand2", command=self.test_connection)
        btn_test.grid(row=0, column=2, padx=5, pady=4)

        self.conn_status = tk.Label(card_conn, text="Portas: RTSP 6666 | TCP Bridge 9911/9922 | Controle 6000", font=("Segoe UI", 8), fg="#888899", bg="#202026", wraplength=540, justify="left")
        self.conn_status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # 2. Card: Controle / Gamepad
        card_pad = tk.LabelFrame(main, text=" 2. Gamepad / Controle no PC ", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#202026", bd=1, relief="solid", padx=15, pady=10)
        card_pad.pack(fill="x", pady=(0, 12))

        self.lbl_gamepad = tk.Label(card_pad, text="🔍 Procurando controle conectado...", font=("Segoe UI", 10, "bold"), fg="#ffaa00", bg="#202026")
        self.lbl_gamepad.pack(anchor="w", pady=(0, 3))

        self.lbl_live_input = tk.Label(card_pad, text="Entrada ao vivo: Aguardando toque no controle...", font=("Segoe UI", 8), fg="#00d26a", bg="#202026")
        self.lbl_live_input.pack(anchor="w", pady=(0, 5))

        row_layout = tk.Frame(card_pad, bg="#202026")
        row_layout.pack(fill="x", pady=2)

        tk.Label(row_layout, text="Mapeamento de Botões:", font=("Segoe UI", 9), fg="#cccccc", bg="#202026").pack(side="left")
        
        self.layout_var = tk.StringVar(value=self.config.get("layout", "nintendo"))
        rb_nintendo = tk.Radiobutton(row_layout, text="Layout Switch (A à direita)", variable=self.layout_var, value="nintendo", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_nintendo.pack(side="left", padx=(15, 5))
        
        rb_xbox = tk.Radiobutton(row_layout, text="Layout Xbox (A embaixo)", variable=self.layout_var, value="xbox", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_xbox.pack(side="left", padx=5)

        # Botões para Parear / Desbloquear Tela de Controle
        frame_pair = tk.Frame(card_pad, bg="#202026")
        frame_pair.pack(fill="x", pady=(6, 0))
        
        self.btn_pair_p1 = tk.Button(frame_pair, text="🎮 Sincronizar Jogador 1 (L + R + A)", font=("Segoe UI", 9, "bold"), bg="#2b6cb0", fg="#ffffff", activebackground="#2c5282", activeforeground="#ffffff", bd=0, padx=10, pady=4, cursor="hand2", command=self.send_pair_p1)
        self.btn_pair_p1.pack(side="left")

        self.btn_cancel_p1 = tk.Button(frame_pair, text="↩ Voltar ao Jogo (B)", font=("Segoe UI", 9, "bold"), bg="#4a5568", fg="#edf2f7", activebackground="#2d3748", activeforeground="#ffffff", bd=0, padx=8, pady=4, cursor="hand2", command=self.send_cancel_applet)
        self.btn_cancel_p1.pack(side="left", padx=(8, 0))

        # 3. Card: Opções de Vídeo e Transmissão
        card_video = tk.LabelFrame(main, text=" 3. Configurações de Transmissão ", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#202026", bd=1, relief="solid", padx=15, pady=10)
        card_video.pack(fill="x", pady=(0, 15))

        row_proto = tk.Frame(card_video, bg="#202026")
        row_proto.pack(fill="x", pady=(0, 4))

        tk.Label(row_proto, text="Transporte RTSP:", font=("Segoe UI", 9), fg="#cccccc", bg="#202026").pack(side="left")
        self.transport_var = tk.StringVar(value=self.config.get("transport", "tcp"))
        rb_tcp = tk.Radiobutton(row_proto, text="TCP (mais estável)", variable=self.transport_var, value="tcp", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_tcp.pack(side="left", padx=(10, 5))
        rb_udp = tk.Radiobutton(row_proto, text="UDP (menor atraso)", variable=self.transport_var, value="udp", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_udp.pack(side="left", padx=5)

        self.fullscreen_var = tk.BooleanVar(value=self.config.get("fullscreen", False))
        cb_fs = tk.Checkbutton(row_proto, text="Tela Cheia Automática", variable=self.fullscreen_var, font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        cb_fs.pack(side="right", padx=5)

        row_player = tk.Frame(card_video, bg="#202026")
        row_player.pack(fill="x", pady=(4, 0))

        tk.Label(row_player, text="Player:", font=("Segoe UI", 9), fg="#cccccc", bg="#202026").pack(side="left")
        self.player_var = tk.StringVar(value=self.config.get("player", "mpv"))
        rb_mpv = tk.Radiobutton(row_player, text="MPV (RTSP)", variable=self.player_var, value="mpv", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_mpv.pack(side="left", padx=(10, 5))
        rb_ffplay = tk.Radiobutton(row_player, text="FFplay (RTSP)", variable=self.player_var, value="ffplay", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_ffplay.pack(side="left", padx=5)
        rb_sysdvr = tk.Radiobutton(row_player, text="SysDVR Client (TCP Bridge)", variable=self.player_var, value="sysdvr", font=("Segoe UI", 9), fg="#ffffff", bg="#202026", selectcolor="#141418", activebackground="#202026", activeforeground="#00d26a")
        rb_sysdvr.pack(side="left", padx=5)

        # 4. Botão de Ação Principal
        self.btn_action = tk.Button(main, text="▶ CONECTAR E JOGAR", font=("Segoe UI", 13, "bold"), bg="#00d26a", fg="#000000", activebackground="#00b058", activeforeground="#000000", bd=0, relief="flat", height=2, cursor="hand2", command=self.toggle_remote_play)
        self.btn_action.pack(fill="x", pady=(4, 4))

        # Status / Feedback
        self.status_bar = tk.Label(main, text="💡 Para menor atraso, experimente TCP Bridge e mantenha o Switch perto do roteador 5 GHz.", font=("Segoe UI", 8), fg="#9999aa", bg="#18181c", wraplength=570, justify="center")
        self.status_bar.pack(pady=(2, 0))

    def send_pair_p1(self):
        """Envia L + R simultâneos e depois A para confirmar e fechar a tela"""
        if self.is_running:
            self.cmd_queue.put("clickSeq +L,+R,W600,-L,-R,W300,A")
            self.lbl_live_input.config(text="🟢 Enviado: L+R simultâneo + A!", fg="#00d26a")
        else:
            ip = self.ip_entry.get().strip()
            if not ip: return
            def run_pair():
                try:
                    s = socket.socket()
                    s.settimeout(2.5)
                    s.connect((ip, 6000))
                    s.sendall(b"clickSeq +L,+R,W600,-L,-R,W300,A\r\n")
                    s.close()
                    self.root.after(0, lambda: self.lbl_live_input.config(text="🟢 Enviado: L+R simultâneo + A!", fg="#00d26a"))
                except Exception as e:
                    self.root.after(0, lambda: self.lbl_live_input.config(text=f"Erro ao enviar L+R: {e}", fg="#ff4444"))
            threading.Thread(target=run_pair, daemon=True).start()

    def send_cancel_applet(self):
        """Envia B para cancelar a tela de mudar ordem e voltar direto ao jogo"""
        if self.is_running:
            self.cmd_queue.put("click B")
            self.lbl_live_input.config(text="🟢 Enviado B (Voltar ao jogo)!", fg="#00d26a")
        else:
            ip = self.ip_entry.get().strip()
            if not ip: return
            def run_b():
                try:
                    s = socket.socket()
                    s.settimeout(2.5)
                    s.connect((ip, 6000))
                    s.sendall(b"click B\r\n")
                    s.close()
                    self.root.after(0, lambda: self.lbl_live_input.config(text="🟢 Enviado B!", fg="#00d26a"))
                except Exception:
                    pass
            threading.Thread(target=run_b, daemon=True).start()

    def start_gamepad_monitor(self):
        """Monitora conexão de gamepads dinamicamente em background"""
        def poll_loop():
            while not self._closing:
                input_alive = self.input_thread is not None and self.input_thread.is_alive()
                if not self.is_running and not input_alive:
                    pygame.event.pump()
                    count = pygame.joystick.get_count()
                    if count > 0 and self.gamepad is None:
                        try:
                            self.gamepad = pygame.joystick.Joystick(0)
                            self.gamepad.init()
                            name = self.gamepad.get_name()
                            self.root.after(0, lambda n=name: self.lbl_gamepad.config(text=f"🟢 Conectado: {n}", fg="#00d26a"))
                        except Exception:
                            pass
                    elif count == 0 and self.gamepad is not None:
                        self.gamepad = None
                        self.root.after(0, lambda: self.lbl_gamepad.config(text="⚠️ Nenhum controle detectado no PC", fg="#ffaa00"))
                time.sleep(1.0)

        t = threading.Thread(target=poll_loop, daemon=True)
        t.start()

    def test_connection(self):
        ip = self.validate_switch_ip()
        if ip is None:
            return

        player_choice = self.player_var.get()
        if player_choice == "sysdvr":
            media_ports = (("vídeo TCP Bridge (9911)", 9911), ("áudio TCP Bridge (9922)", 9922))
        else:
            media_ports = (("RTSP (6666)", 6666),)

        self.conn_status.config(text=f"Testando portas do Switch {ip}...", fg="#ffaa00")

        def test_task():
            botbase_ok = self.probe_tcp_port(ip, 6000)
            media_results = [(label, self.probe_tcp_port(ip, port)) for label, port in media_ports]
            media_ok = all(result for _, result in media_results)
            control_status = "controle 6000: OK" if botbase_ok else "controle 6000: indisponível"
            media_status = ", ".join(
                f"{label}: {'OK' if result else 'indisponível'}"
                for label, result in media_results
            )
            self.log_message(f"Teste de portas {ip}: {control_status}; {media_status}.")

            if media_ok and botbase_ok:
                msg = f"✅ {control_status}; {media_status}. Abra um jogo compatível."
                color = "#00d26a"
            elif media_ok:
                msg = f"⚠️ {media_status}; {control_status}."
                color = "#ffaa00"
            else:
                msg = f"❌ {media_status}; {control_status}. Confira modo SysDVR e rede."
                color = "#ff4444"

            try:
                self.root.after(0, lambda text=msg, fg=color: self.conn_status.config(text=text, fg=fg))
            except Exception:
                pass

        threading.Thread(target=test_task, daemon=True).start()

    def toggle_remote_play(self):
        if self.is_running:
            self.stop_remote_play()
        else:
            self.start_remote_play()

    def start_remote_play(self):
        ip = self.validate_switch_ip()
        if ip is None:
            return

        self.save_config()
        transport = self.transport_var.get()
        player_choice = self.player_var.get()
        rtsp_url = f"rtsp://{ip}:6666/"

        if player_choice == "mpv":
            if not os.path.exists(MPV_PATH):
                messagebox.showerror("Player não encontrado", f"O MPV não foi encontrado em:\n{MPV_PATH}")
                return
            cmd = [
                MPV_PATH,
                "--profile=low-latency",
                "--cache=no",
                "--force-window=immediate",
                f"--rtsp-transport={transport}",
                f"--log-file={PLAYER_LOG_FILE}",
                "--title=Nintendo Switch - Remote Play",
                rtsp_url,
            ]
            if self.fullscreen_var.get():
                cmd.append("--fullscreen")
        elif player_choice == "ffplay":
            if not os.path.exists(FFPLAY_PATH):
                messagebox.showerror("Player não encontrado", f"O FFplay não foi encontrado em:\n{FFPLAY_PATH}")
                return
            cmd = [
                FFPLAY_PATH,
                "-rtsp_transport", transport,
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-framedrop",
                "-sync", "ext",
                "-window_title", "Nintendo Switch - Remote Play",
                "-loglevel", "warning",
                rtsp_url,
            ]
            if self.fullscreen_var.get():
                cmd.append("-fs")
        elif player_choice == "sysdvr":
            if not os.path.exists(SYSDVR_CLIENT_PATH):
                messagebox.showerror("SysDVR-Client não encontrado", f"O cliente não foi encontrado em:\n{SYSDVR_CLIENT_PATH}")
                return
            cmd = [
                SYSDVR_CLIENT_PATH,
                "bridge",
                ip,
                # The new SysDVR GUI also consumes gamepad input. Use its legacy
                # video player so the controller is handled only by this app.
                "--legacy",
            ]
            if self.fullscreen_var.get():
                cmd.append("--fullscreen")
        else:
            messagebox.showerror("Player inválido", "Selecione MPV, FFplay ou SysDVR Client.")
            return

        try:
            self.log_message(f"Iniciando player={player_choice}, IP={ip}, transporte={transport}.")
            player_env = os.environ.copy()
            if player_choice == "sysdvr":
                # This setting is needed by pygame in this process, but would
                # also make SysDVR's SDL UI react to the same controller.
                player_env.pop("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", None)
            process = subprocess.Popen(
                cmd,
                cwd=APP_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=player_env,
            )
        except Exception as e:
            self.log_message(f"Falha ao iniciar player {player_choice}: {e}")
            messagebox.showerror("Erro ao iniciar player", str(e))
            return

        self._player_session += 1
        session_id = self._player_session
        self.video_process = process
        self.is_running = True
        self.btn_action.config(text="⏹ PARAR REMOTE PLAY", bg="#ff3344", activebackground="#cc2233")
        if player_choice == "sysdvr":
            status = "TCP Bridge iniciado; no Switch selecione o modo TCP Bridge do SysDVR."
        else:
            status = f"Player iniciado via RTSP/{transport.upper()}; aguardando quadros do Switch."
        self.status_bar.config(text=status, fg="#00d26a")

        self.input_thread = threading.Thread(target=self.gamepad_loop, args=(ip, session_id), daemon=True)
        self.input_thread.start()

        self.watch_player_process(process, session_id, player_choice, ip)

    def watch_player_process(self, process, session_id, player_choice, ip, video_only=False):
        audio_endpoint_error = threading.Event()
        output_finished = threading.Event()

        def capture_player_output():
            try:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    output = line.rstrip()
                    if not output:
                        continue
                    self.log_message(f"PLAYER: {output}")
                    output_lower = output.lower()
                    if player_choice == "sysdvr" and "wasapi" in output_lower and "endpoint" in output_lower:
                        audio_endpoint_error.set()
                        self.log_message("Saída de áudio WASAPI indisponível; o vídeo será reiniciado sem áudio.")
                    if (
                        player_choice == "sysdvr"
                        and "player creation failed" in output_lower
                        and not audio_endpoint_error.is_set()
                    ):
                        failure = "O SysDVR-Client não conseguiu criar a janela do player. Consulte remote_play.log."
                        self.root.after(
                            0,
                            lambda message=failure: self.handle_player_startup_failure(
                                session_id, process, message
                            ),
                        )
                        return
            except Exception as e:
                self.log_message(f"Não foi possível ler a saída do player: {e}")
            finally:
                output_finished.set()

        def monitor_video():
            return_code = process.wait()
            output_finished.wait(timeout=2.0)
            self.log_message(f"Player encerrado com código {return_code}.")
            if player_choice == "sysdvr" and audio_endpoint_error.is_set() and not video_only:
                try:
                    self.root.after(
                        0,
                        lambda: self.start_sysdvr_video_only(session_id, process, ip),
                    )
                except Exception:
                    pass
                return
            if self.video_process is process:
                self.video_process = None
            try:
                self.root.after(0, lambda: self.handle_player_exit(session_id, return_code, player_choice))
            except Exception:
                pass

        threading.Thread(target=capture_player_output, daemon=True).start()
        threading.Thread(target=monitor_video, daemon=True).start()

    def start_sysdvr_video_only(self, session_id, failed_process, ip):
        if (
            not self.is_running
            or session_id != self._player_session
            or self.video_process is not failed_process
        ):
            return

        cmd = [SYSDVR_CLIENT_PATH, "bridge", ip, "--no-audio", "--legacy"]
        if self.fullscreen_var.get():
            cmd.append("--fullscreen")
        player_env = os.environ.copy()
        player_env.pop("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", None)
        try:
            process = subprocess.Popen(
                cmd,
                cwd=APP_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=player_env,
            )
        except Exception as e:
            self.log_message(f"Falha ao iniciar SysDVR apenas com vídeo: {e}")
            self.video_process = None
            self.stop_remote_play("Não foi possível iniciar o vídeo. Consulte remote_play.log.", error=True)
            messagebox.showerror("Falha ao abrir vídeo", str(e))
            return

        self.video_process = process
        self.status_bar.config(
            text="Vídeo iniciado sem áudio: o Windows não encontrou uma saída WASAPI. Habilite uma saída de som e reconecte para recuperar o áudio.",
            fg="#ffaa00",
        )
        self.log_message("SysDVR reiniciado em modo somente vídeo para evitar fechar a transmissão.")
        self.watch_player_process(process, session_id, "sysdvr", ip, video_only=True)

    def handle_player_startup_failure(self, session_id, process, message):
        if not self.is_running or session_id != self._player_session or self.video_process is not process:
            return

        self.log_message(f"Falha ao iniciar player: {message}")
        self.stop_remote_play(message, error=True)
        messagebox.showerror("Falha ao abrir vídeo/áudio", message)

    def handle_player_exit(self, session_id, return_code, player_choice):
        if not self.is_running or session_id != self._player_session:
            return

        if return_code:
            message = (
                f"O player encerrou com código {return_code}. Consulte remote_play.log"
                + (" e player_debug.log." if player_choice == "mpv" else ".")
            )
            self.stop_remote_play(message, error=True)
            messagebox.showerror("Falha na transmissão", message)
        else:
            self.stop_remote_play("Player fechado; transmissão encerrada.")

    def gamepad_loop(self, ip, session_id):
        """Loop responsivo de gamepad com envio imediato de botões e sticks a 60 Hz otimizados"""
        app_log = self.log_message
        app_log(f"Iniciando gamepad_loop para {ip}...")
        sock = None
        last_connect_attempt = -1.0
        last_connect_error_log = 0.0
        last_gamepad_error_log = 0.0
        last_gamepad_init_error_log = 0.0

        def get_socket():
            nonlocal sock, last_connect_attempt, last_connect_error_log
            if sock is not None:
                try:
                    r, _, _ = select.select([sock], [], [], 0)
                    if r:
                        peek = sock.recv(1024, socket.MSG_PEEK)
                        if len(peek) == 0:
                            sock.close()
                            if self.botbase_socket is sock:
                                self.botbase_socket = None
                            sock = None
                except Exception:
                    try:
                        sock.close()
                    except Exception:
                        pass
                    if self.botbase_socket is sock:
                        self.botbase_socket = None
                    sock = None

            if sock is not None:
                return sock

            now = time.monotonic()
            if now - last_connect_attempt < 0.5:
                return None
            last_connect_attempt = now
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(1.5)
                s.connect((ip, 6000))
                sock = s
                self.botbase_socket = s
                app_log(f"Socket TCP conectado a {ip}:6000!")
                self.root.after(0, lambda: self.lbl_live_input.config(text="Controle conectado ao Switch!", fg="#00d26a"))
                return sock
            except Exception as e:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
                sock = None
                now = time.monotonic()
                if now - last_connect_error_log >= 5.0:
                    app_log(f"Falha ao conectar ao sys-botbase {ip}:6000: {e}")
                    last_connect_error_log = now
                return None

        def send_cmd(cmd_str):
            s = get_socket()
            if s is None:
                return False
            try:
                s.sendall((cmd_str + "\r\n").encode())
                return True
            except Exception as e:
                nonlocal sock
                try:
                    s.close()
                except Exception:
                    pass
                    if self.botbase_socket is s:
                        self.botbase_socket = None
                sock = None
                app_log(f"Erro no envio de comando ({cmd_str}): {e}")
                return False

        prev_buttons = {}
        prev_left_stick = (0, 0)
        prev_right_stick = (0, 0)
        last_stick_send_time = 0.0
        deadzone = float(self.config.get("deadzone", 0.08))

        # Estabelece conexão inicial silenciosa sem desanexar controles
        get_socket()

        while self.is_running and session_id == self._player_session:
            # 0. Processa comandos manuais da fila (ex: Sincronização L + R da interface)
            while not self.cmd_queue.empty():
                try:
                    queued_cmd = self.cmd_queue.get_nowait()
                    send_cmd(queued_cmd)
                except Exception:
                    break

            # 1. Garante leitura de eventos SDL
            pygame.event.pump()

            # 2. Garante instância do Joystick
            if self.gamepad is None:
                if pygame.joystick.get_count() > 0:
                    try:
                        self.gamepad = pygame.joystick.Joystick(0)
                        self.gamepad.init()
                        app_log(f"Gamepad detectado no loop: {self.gamepad.get_name()}")
                        self.root.after(0, lambda: self.lbl_gamepad.config(text=f"🟢 Conectado: {self.gamepad.get_name()}", fg="#00d26a"))
                    except Exception as e:
                        now = time.monotonic()
                        if now - last_gamepad_init_error_log >= 5.0:
                            app_log(f"Erro ao inicializar joystick: {e}")
                            last_gamepad_init_error_log = now
                time.sleep(0.05)
                continue

            layout = LAYOUT_NINTENDO if self.layout_var.get() == "nintendo" else LAYOUT_XBOX_STYLE

            # 3. Leitura Instantânea de Botões Digitais
            try:
                num_buttons = self.gamepad.get_numbuttons()
                for btn_idx in range(num_buttons):
                    curr_state = bool(self.gamepad.get_button(btn_idx))
                    old_state = prev_buttons.get(btn_idx, False)

                    if curr_state != old_state:
                        prev_buttons[btn_idx] = curr_state
                        switch_btn = layout.get(btn_idx)
                        if switch_btn:
                            action = "press" if curr_state else "release"
                            send_cmd(f"{action} {switch_btn}")
                            if curr_state:
                                self.root.after(0, lambda b=switch_btn: self.lbl_live_input.config(text=f"🟢 Botão: {b}", fg="#00d26a"))

                # 4. Leitura Instantânea de Gatilhos Analógicos (L2 e R2)
                num_axes = self.gamepad.get_numaxes()
                if num_axes >= 6:
                    lt_val = self.gamepad.get_axis(4)
                    rt_val = self.gamepad.get_axis(5)
                    
                    lt_pressed = lt_val > 0.0
                    if lt_pressed != prev_buttons.get("LT", False):
                        prev_buttons["LT"] = lt_pressed
                        send_cmd(f"{'press' if lt_pressed else 'release'} ZL")
                        if lt_pressed:
                            self.root.after(0, lambda: self.lbl_live_input.config(text="🟢 Gatilho: ZL (L2)", fg="#00d26a"))

                    rt_pressed = rt_val > 0.0
                    if rt_pressed != prev_buttons.get("RT", False):
                        prev_buttons["RT"] = rt_pressed
                        send_cmd(f"{'press' if rt_pressed else 'release'} ZR")
                        if rt_pressed:
                            self.root.after(0, lambda: self.lbl_live_input.config(text="🟢 Gatilho: ZR (R2)", fg="#00d26a"))

                # 5. Leitura Instantânea do D-Pad
                num_hats = self.gamepad.get_numhats()
                if num_hats > 0:
                    hat_x, hat_y = self.gamepad.get_hat(0)
                    dup_pressed = (hat_y == 1)
                    ddown_pressed = (hat_y == -1)
                    dleft_pressed = (hat_x == -1)
                    dright_pressed = (hat_x == 1)

                    for name, state in [("DUP", dup_pressed), ("DDOWN", ddown_pressed), ("DLEFT", dleft_pressed), ("DRIGHT", dright_pressed)]:
                        if state != prev_buttons.get(name, False):
                            prev_buttons[name] = state
                            send_cmd(f"{'press' if state else 'release'} {name}")
                            if state:
                                self.root.after(0, lambda n=name: self.lbl_live_input.config(text=f"🟢 D-Pad: {n}", fg="#00d26a"))

                # 6. Analógicos com cadência ótima (60 Hz / ~16ms) para eliminar delay e saturação Wi-Fi
                now = time.monotonic()
                if num_axes >= 4 and (now - last_stick_send_time >= 0.016):
                    lx = self.gamepad.get_axis(0)
                    ly = self.gamepad.get_axis(1)
                    rx = self.gamepad.get_axis(2)
                    ry = self.gamepad.get_axis(3)

                    if abs(lx) < deadzone: lx = 0.0
                    if abs(ly) < deadzone: ly = 0.0
                    if abs(rx) < deadzone: rx = 0.0
                    if abs(ry) < deadzone: ry = 0.0

                    sw_lx = int(lx * 32767)
                    sw_ly = int(-ly * 32767)
                    sw_rx = int(rx * 32767)
                    sw_ry = int(-ry * 32767)

                    stick_sent = False
                    # Envia Left Stick se alterou significativamente ou se voltou ao centro
                    if abs(sw_lx - prev_left_stick[0]) > 800 or abs(sw_ly - prev_left_stick[1]) > 800 or ((sw_lx == 0 and sw_ly == 0) and prev_left_stick != (0, 0)):
                        prev_left_stick = (sw_lx, sw_ly)
                        send_cmd(f"setStick LEFT {sw_lx} {sw_ly}")
                        stick_sent = True

                    # Envia Right Stick se alterou significativamente ou se voltou ao centro
                    if abs(sw_rx - prev_right_stick[0]) > 800 or abs(sw_ry - prev_right_stick[1]) > 800 or ((sw_rx == 0 and sw_ry == 0) and prev_right_stick != (0, 0)):
                        prev_right_stick = (sw_rx, sw_ry)
                        send_cmd(f"setStick RIGHT {sw_rx} {sw_ry}")
                        stick_sent = True

                    if stick_sent:
                        last_stick_send_time = now

            except Exception as e:
                now = time.monotonic()
                if now - last_gamepad_error_log >= 5.0:
                    app_log(f"Erro no loop do gamepad: {e}")
                    last_gamepad_error_log = now
                self.gamepad = None
                prev_buttons.clear()
                time.sleep(0.1)

            time.sleep(0.005)

        # Encerramento seguro: centraliza analógicos
        if sock:
            try:
                if session_id == self._player_session:
                    send_cmd("setStick LEFT 0 0")
                    send_cmd("setStick RIGHT 0 0")
                sock.close()
            except Exception:
                pass
        if self.botbase_socket is sock:
            self.botbase_socket = None
        app_log("gamepad_loop encerrado.")

    def stop_remote_play(self, status_text=None, error=False):
        self.is_running = False
        process = self.video_process
        self.video_process = None
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

        self.btn_action.config(text="▶ CONECTAR E JOGAR", bg="#00d26a", activebackground="#00b058")
        text = status_text or "Transmissão encerrada. Clique para reconectar quando quiser."
        self.status_bar.config(text=text, fg="#ff4444" if error else "#777788")

    def close_app(self):
        self._closing = True
        self.stop_remote_play()
        self.root.destroy()

def main():
    root = tk.Tk()
    app = RemotePlayApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close_app)
    root.mainloop()

if __name__ == "__main__":
    main()
