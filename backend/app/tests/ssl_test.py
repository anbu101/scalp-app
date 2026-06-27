import ssl
import traceback
import tkinter as tk
from tkinter import messagebox

root = tk.Tk()
root.withdraw()

try:
    version = ssl.OPENSSL_VERSION
    messagebox.showinfo(
        "SSL Test",
        f"SUCCESS!\n\nOpenSSL Version:\n{version}"
    )
except Exception:
    messagebox.showerror(
        "SSL Test Failed",
        traceback.format_exc()
    )