
import base64
import io
from pathlib import Path
from modules import shared,script_callbacks,scripts as md_scripts,images
from modules.api import api
from modules.shared import opts
from scripts.core.core import get_sha256,dencrypt_image,dencrypt_image_v2,encrypt_image_v2
from PIL import PngImagePlugin,_util,ImagePalette
from PIL import Image as PILImage
from io import BytesIO
from typing import Optional
from fastapi import FastAPI
from gradio import Blocks
from fastapi import FastAPI, Request, Response
import sys
from urllib.parse import unquote

repo_dir = md_scripts.basedir()
password = getattr(shared.cmd_opts, 'enc_pw', None)


def hook_http_request(app: FastAPI):
    @app.middleware("http")
    async def image_dencrypt(req: Request, call_next):
        endpoint:str = req.scope.get('path', 'err')
        # 兼容无边浏览器
        if endpoint.startswith('/infinite_image_browsing/image-thumbnail') or endpoint.startswith('/infinite_image_browsing/file'):
            query_string:str = req.scope.get('query_string').decode('utf-8')
            query_string = unquote(query_string)
            if query_string and query_string.index('path=')>=0:
                query = query_string.split('&')
                path = ''
                for sub in query:
                    if sub.startswith('path='):
                        path = sub[sub.index('=')+1:]
                if path:
                    endpoint = '/file=' + path
        # 模型预览图
        if endpoint.startswith('/sd_extra_networks/thumb'):
            query_string:str = req.scope.get('query_string').decode('utf-8')
            query_string = unquote(query_string)
            if query_string and query_string.index('filename=')>=0:
                query = query_string.split('&')
                path = ''
                for sub in query:
                    if sub.startswith('filename='):
                        path = sub[sub.index('=')+1:]
                if path:
                    endpoint = '/file=' + path
        if endpoint.startswith('/file='):
            file_path = endpoint[6:] or ''
            if not file_path: return await call_next(req)
            if file_path.rfind('.') == -1: return await call_next(req)
            if not file_path[file_path.rfind('.'):]: return await call_next(req)
            if file_path[file_path.rfind('.'):].lower() in ['.png','.jpg','.jpeg','.webp','.abcd']:
                image = PILImage.open(file_path)
                pnginfo = image.info or {}
                if 'Encrypt' in pnginfo:
                    buffered = BytesIO()
                    info = PngImagePlugin.PngInfo()
                    for key in pnginfo.keys():
                        if pnginfo[key]:
                            info.add_text(key,pnginfo[key])
                    image.save(buffered, format=PngImagePlugin.PngImageFile.format, pnginfo=info)
                    decrypted_image_data = buffered.getvalue()
                    response: Response = Response(content=decrypted_image_data, media_type="image/png")
                    return response
        
        return await call_next(req)
    
def set_shared_options():
    # 传递插件状态到前端
    section = ("encrypt_image_is_enable",'图片加密' if shared.opts.localization == 'zh_CN' else "encrypt image" )
    option = shared.OptionInfo(
            default="是",
            label='是否启用了加密插件' if shared.opts.localization == 'zh_CN' else "Whether the encryption plug-in is enabled",
            section=section,
        )
    option.do_not_save = True
    shared.opts.add_option(
        "encrypt_image_is_enable",
        option,
    )
    shared.opts.data['encrypt_image_is_enable'] = "是"

def app_started_callback(_: Blocks, app: FastAPI):
    set_shared_options()
    

if PILImage.Image.__name__ != 'EncryptedImage':
    super_open = PILImage.open
    super_encode_pil_to_base64 = api.encode_pil_to_base64
    super_modules_images_save_image = images.save_image
    super_api_middleware = api.api_middleware
    class EncryptedImage(PILImage.Image):
        __name__ = "EncryptedImage"
        
        @staticmethod
        def from_image(image:PILImage.Image):
            image = image.copy()
            img = EncryptedImage()
            img.im = image.im
            img._mode = image.mode
            if image.im.mode:
                try:
                    img.mode = image.im.mode
                except Exception as e:
                    ''
            img._size = image.size
            img.format = image.format
            if image.mode in ("P", "PA"):
                if image.palette:
                    img.palette = image.palette.copy()
                else:
                    img.palette = ImagePalette.ImagePalette()
            img.info = image.info.copy()
            return img
            
        def save(self, fp, format=None, **params):
            filename = ""
            if isinstance(fp, Path):
                filename = str(fp)
            elif _util.is_path(fp):
                filename = fp
            elif fp == sys.stdout:
                try:
                    fp = sys.stdout.buffer
                except AttributeError:
                    pass
            if not filename and hasattr(fp, "name") and _util.is_path(fp.name):
                # only set the name for metadata purposes
                filename = fp.name
            
            if not filename or not password:
                # 如果没有密码或不保存到硬盘，直接保存
                super().save(fp, format = format, **params)
                return
            
            if 'Encrypt' in self.info and (self.info['Encrypt'] == 'pixel_shuffle' or self.info['Encrypt'] == 'pixel_shuffle_2'):
                super().save(fp, format = format, **params)
                return
            
            encrypt_image_v2(self, get_sha256(password))
            self.format = PngImagePlugin.PngImageFile.format
            pnginfo = params.get('pnginfo', PngImagePlugin.PngInfo())
            if not pnginfo:
                pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text('Encrypt', 'pixel_shuffle_2')
            pnginfo.add_text('EncryptPwdSha', get_sha256(f'{get_sha256(password)}Encrypt'))
            for key in (self.info or {}).keys():
                if self.info[key]:
                    pnginfo.add_text(key,str(self.info[key]))
            params.update(pnginfo=pnginfo)
            super().save(fp, format=self.format, **params)
            # 保存到文件后解密内存内的图片，让直接在内存内使用时图片正常
            dencrypt_image_v2(self, get_sha256(password))
            


    def open(fp,*args, **kwargs):
        image = super_open(fp,*args, **kwargs)
        if password and image.format.lower() == PngImagePlugin.PngImageFile.format.lower():
            pnginfo = image.info or {}
            if 'Encrypt' in pnginfo and pnginfo["Encrypt"] == 'pixel_shuffle':
                dencrypt_image(image, get_sha256(password))
                pnginfo["Encrypt"] = None
                image = EncryptedImage.from_image(image=image)
                return image
            if 'Encrypt' in pnginfo and pnginfo["Encrypt"] == 'pixel_shuffle_2':
                dencrypt_image_v2(image, get_sha256(password))
                pnginfo["Encrypt"] = None
                image = EncryptedImage.from_image(image=image)
                return image
        return EncryptedImage.from_image(image=image)
    
    def encode_pil_to_base64(image:PILImage.Image):
        with io.BytesIO() as output_bytes:
            image.save(output_bytes, format="PNG", quality=opts.jpeg_quality)
            pnginfo = image.info or {}
            if 'Encrypt' in pnginfo and pnginfo["Encrypt"] == 'pixel_shuffle':
                dencrypt_image(image, get_sha256(password))
                pnginfo["Encrypt"] = None
            if 'Encrypt' in pnginfo and pnginfo["Encrypt"] == 'pixel_shuffle_2':
                dencrypt_image_v2(image, get_sha256(password))
                pnginfo["Encrypt"] = None
            bytes_data = output_bytes.getvalue()
        return base64.b64encode(bytes_data)
  
    def api_middleware(app: FastAPI):
        super_api_middleware(app)
        hook_http_request(app)
  
    if password:
        PILImage.Image = EncryptedImage
        PILImage.open = open
        api.encode_pil_to_base64 = encode_pil_to_base64
        api.api_middleware = api_middleware
        
if password:
    script_callbacks.on_app_started(app_started_callback)
    print('图片加密已经启动 加密方式 2')

else:
    print('图片加密插件已安装，但缺少密码参数未启动')
