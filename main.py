import os
import sys

# Force Python network libraries and downloaders to use the bundled certifi bundle
if getattr(sys, 'frozen', False):
    bundled_cert = os.path.join(sys._MEIPASS, 'certifi', 'cacert.pem')
    if os.path.exists(bundled_cert):
        os.environ['SSL_CERT_FILE'] = bundled_cert
        os.environ['REQUESTS_CA_BUNDLE'] = bundled_cert

from browser import JoshWBrowser


if __name__ == "__main__":
    app = JoshWBrowser()
    app.mainloop()