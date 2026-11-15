import os
import sys

# Force Python network libraries and downloaders to use the bundled certifi bundle
if getattr(sys, 'frozen', False):
    bundled_cert = os.path.join(sys._MEIPASS, 'certifi', 'cacert.pem')
    if os.path.exists(bundled_cert):
        os.environ['SSL_CERT_FILE'] = bundled_cert
        os.environ['REQUESTS_CA_BUNDLE'] = bundled_cert



if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--player":
        # A player launched from the browser in a PyInstaller build
        from player_launcher import run_player_from_main
        run_player_from_main(sys.argv[2:])
    else:
        from browser import JoshWBrowser
        app = JoshWBrowser()
        app.mainloop()
        # The window is gone: end the process even if a library thread (tray
        # icon, network worker) is still alive. Players are separate processes
        # and keep running.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)