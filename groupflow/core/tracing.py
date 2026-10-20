"""Bounded request correlation without logging credentials, URLs or bodies."""
from contextvars import ContextVar
import logging
import time
import uuid

from starlette.responses import JSONResponse

REQUEST_ID = ContextVar('request_id',default='')
log=logging.getLogger('groupflow.http')


class RequestTrace:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        ident=uuid.uuid4().hex
        ctx=REQUEST_ID.set(ident)
        scope.setdefault('state',{})['request_id']=ident
        started=time.perf_counter()
        status=500
        response_started=False
        complete=False
        disconnected=False
        count=0
        async def traced_receive():
            nonlocal disconnected
            message=await receive()
            disconnected|=message['type']=='http.disconnect'
            return message
        async def traced_send(message):
            nonlocal status,response_started,complete,count
            if message['type']=='http.response.start':
                response_started=True
                status=message['status']
                message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'x-request-id']+[(b'x-request-id',ident.encode())]}
            elif message['type']=='http.response.body':
                count+=len(message.get('body',b''))
                complete=not message.get('more_body',False)
            await send(message)
        try:
            await self.app(scope,traced_receive,traced_send)
        except Exception:
            if response_started:
                raise
            await JSONResponse({'error':{'code':'INTERNAL','message':'Unexpected server error. Use the request ID to report it.'}},status_code=500)(scope,traced_receive,traced_send)
        finally:
            # Paths can contain calendar bearer tokens. Keep only a fixed route family.
            family='mcp' if scope.get('path','').startswith('/mcp') else 'human' if scope.get('path','').startswith('/human') else 'calendar' if scope.get('path','').startswith('/calendar') else 'other'
            log.info('request_id=%s family=%s method=%s status=%d complete=%s disconnected=%s bytes=%d ms=%.1f',ident,family,scope['method'],status,complete,disconnected,count,(time.perf_counter()-started)*1000)
            REQUEST_ID.reset(ctx)
