# -*- coding: utf-8 -*-
import base64
from Crypto.Cipher import DES, AES
from Crypto.Util.Padding import pad

def encrypt_aes_base64(plaintext: str) -> str:
    """
    ChaoXing 登录密码 AES 加密
    """
    key_iv = b"u2oh6Vu^HWe4_AES"
    cipher = AES.new(key_iv, AES.MODE_CBC, key_iv)
    padded = pad(plaintext.encode("utf-8"), AES.block_size, style="pkcs7")
    ciphertext = cipher.encrypt(padded)
    return base64.b64encode(ciphertext).decode("utf-8")


def encrypt_des_hex(plaintext: str, key_str: str = "QRCODENC") -> str:
    """
    DES Hex 加密
    """
    key = key_str.encode("utf-8")
    cipher = DES.new(key, DES.MODE_ECB)
    padded = pad(plaintext.encode("utf-8"), DES.block_size, style="pkcs7")
    ciphertext = cipher.encrypt(padded)
    return ciphertext.hex()
