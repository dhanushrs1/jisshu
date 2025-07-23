import re
import asyncio
import base64
import mimetypes
import logging
from aiohttp import web
from Jisshu.bot import JisshuBot
from Jisshu.util.file_properties import get_file_ids
from Jisshu.util.render_template import render_template
from info import *

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({"status": "running", "rebranded_by": "HD Cinema"})

async def get_file_info(file_id):
    """Helper to get file properties."""
    return await get_file_ids(JisshuBot, int(LOG_CHANNEL), file_id)

@routes.get(r"/watch/{id}")
async def watch_handler(request):
    try:
        id_str = request.match_info['id']
        # Use a simple base64 encoding for the URL, which is less complex but still obfuscates the direct file_id
        decoded_id = base64.urlsafe_b64decode(id_str).decode('ascii')
        file_id = int(decoded_id)
        
        file_info = await get_file_info(file_id)
        if not file_info:
            return web.HTTPNotFound(text="File not found or access denied.")
            
        file_name = file_info.file_name.replace('_', ' ').replace('.',' ').replace('-',' ').title()
        
        # Use human-readable format for file size
        def format_bytes(size):
            if size == 0:
                return "0B"
            size_name = ("B", "KB", "MB", "GB", "TB")
            i = int(math.floor(math.log(size, 1024)))
            p = math.pow(1024, i)
            s = round(size / p, 2)
            return f"{s} {size_name[i]}"

        file_size = format_bytes(file_info.file_size)
        
        stream_url = f"{URL}dl/{id_str}"

        # Render the new, self-contained template
        return web.Response(
            text=await render_template(
                'req.html',
                file_name=file_name,
                file_size=file_size,
                file_url=stream_url
            ),
            content_type='text/html'
        )
    except Exception as e:
        logging.error(f"Error in /watch handler: {e}", exc_info=True)
        return web.HTTPInternalServerError(text="An internal server error occurred.")


@routes.get(r"/dl/{id}")
async def stream_handler(request):
    try:
        id_str = request.match_info['id']
        decoded_id = base64.urlsafe_b64decode(id_str).decode('ascii')
        file_id = int(decoded_id)
        
        return await media_streamer(request, file_id)
    except Exception as e:
        logging.error(f"Error in /dl handler: {e}", exc_info=True)
        return web.HTTPNotFound(text="Invalid download link or file not found.")

async def media_streamer(request, file_id: int):
    range_header = request.headers.get('Range', 0)
    
    file_info = await get_file_info(file_id)
    if not file_info:
        raise web.HTTPNotFound

    file_size = file_info.file_size
    
    if range_header:
        from_bytes, until_bytes = range_header.replace('bytes=', '').split('-')
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    if (until_bytes > file_size) or (from_bytes < 0):
        return web.Response(status=416, text="Requested range not satisfiable")

    req_length = until_bytes - from_bytes + 1
    
    try:
        stream = JisshuBot.stream_media(file_info.message, offset=from_bytes)
        
        body = web.StreamResponse()
        mime_type = file_info.mime_type or mimetypes.guess_type(file_info.file_name)[0] or "application/octet-stream"
        
        body.headers.update({
            "Content-Type": mime_type,
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'attachment; filename="{file_info.file_name}"',
            "Accept-Ranges": "bytes",
        })

        await body.prepare(request)
        
        streamed_bytes = 0
        async for chunk in stream:
            if streamed_bytes + len(chunk) > req_length:
                chunk = chunk[:req_length - streamed_bytes]
            
            try:
                await body.write(chunk)
                streamed_bytes += len(chunk)
            except (asyncio.CancelledError, ConnectionResetError):
                # Client disconnected
                break
        
        return body

    except Exception as e:
        logging.error(f"Streaming error: {e}", exc_info=True)
        return web.HTTPInternalServerError(text="Error while streaming file.")
