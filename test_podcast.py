"""测试火山引擎播客大模型 WebSocket"""
import asyncio, json, uuid, time, sys, os, traceback
sys.path.insert(0, '.')
import websockets
from protocols import (
    start_connection, finish_connection,
    start_session, finish_session,
    receive_message, wait_for_event,
    EventType, MsgType,
)

CONFIG = {
    "app_id": os.getenv("HARDCODED_VOLC_APP_ID", ""),
    "access_token": os.getenv("HARDCODED_VOLC_ACCESS_KEY", ""),
    "app_key": os.getenv("HARDCODED_VOLC_APP_KEY", ""),
    "resource_id": "volc.service_type.10050",
}

async def main():
    session_id = uuid.uuid4().hex
    headers = {
        'X-Api-App-Id': CONFIG['app_id'],
        'X-Api-App-Key': CONFIG['app_key'],
        'X-Api-Access-Key': CONFIG['access_token'],
        'X-Api-Resource-Id': CONFIG['resource_id'],
        'X-Api-Connect-Id': str(uuid.uuid4()),
    }
    
    req_params = {
        'input_id': f'podcast_{int(time.time())}',
        'input_text': '人工智能对未来教育的影响',
        'nlp_texts': None,
        'prompt_text': '',
        'action': 0,
        'use_head_music': False,
        'use_tail_music': False,
        'input_info': {'input_url': '', 'return_audio_url': False, 'only_nlp_text': False},
        'speaker_info': {'random_order': False},
        'audio_config': {'format': 'mp3', 'sample_rate': 24000, 'speech_rate': 0},
    }

    uri = 'wss://openspeech.bytedance.com/api/v3/sami/podcasttts'
    print(f'[1] Connecting to {uri}...')
    
    try:
        ws = await websockets.connect(uri, additional_headers=headers)
        print('[1] Connected!')
    except Exception as e:
        print(f'[1] FAILED: {e}')
        return

    try:
        print('[2] start_connection...')
        await start_connection(ws)
        m = await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)
        print(f'[2] ConnectionStarted, id={m.connect_id}')

        print('[3] start_session...')
        await start_session(ws, json.dumps(req_params).encode(), session_id)
        m = await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)
        print(f'[3] SessionStarted: {m.payload.decode()[:200]}')

        print('[4] finish_session...')
        await finish_session(ws, session_id)

        print('[5] Receiving podcast data...')
        audio_all = bytearray()
        texts = []
        usage = {}

        while True:
            m = await receive_message(ws)
            if m.event == EventType.PodcastRoundStart:
                data = json.loads(m.payload.decode())
                texts.append(data)
                print(f'    Round {data.get("round_id","?")}: {data.get("speaker","?")}: {data.get("text","")[:60]}...')
            elif m.event == EventType.PodcastRoundResponse:
                audio_all.extend(m.payload)
            elif m.event == EventType.PodcastRoundEnd:
                data = json.loads(m.payload.decode())
                print(f'    RoundEnd: dur={data.get("audio_duration",0)}s')
            elif m.event == EventType.PodcastEnd:
                print(f'    PodcastEnd')
            elif m.event == EventType.UsageResponse:
                usage = json.loads(m.payload.decode())
                print(f'    Usage: {usage}')
            elif m.event == EventType.SessionFinished:
                print(f'    SessionFinished')
                break
            elif m.event == EventType.SessionFailed:
                print(f'    SessionFailed: {m.payload.decode()}')
                break
            else:
                print(f'    Event={m.event.name}, type={m.type.name}')
                if m.payload:
                    try:
                        print(f'      payload: {m.payload.decode()[:200]}')
                    except:
                        pass

        print(f'[6] finish_connection...')
        await finish_connection(ws)
        try:
            await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionFinished)
            print('[6] ConnectionFinished')
        except:
            pass

        await ws.close()

        print(f'\n=== RESULT ===')
        print(f'Audio: {len(audio_all)} bytes ({len(audio_all)/1024:.1f} KB)')
        print(f'Text segments: {len(texts)}')
        print(f'Usage: {usage}')
        
        if audio_all:
            out_path = 'test_podcast.mp3'
            with open(out_path, 'wb') as f:
                f.write(audio_all)
            print(f'Saved: {out_path}')
        
        for t in texts:
            print(f'  [{t.get("speaker","?")}] {t.get("text","")}')

    except Exception as e:
        print(f'ERROR: {e}')
        traceback.print_exc()

asyncio.run(main())
