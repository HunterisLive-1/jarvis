import asyncio
import os
import pyaudio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from prompts import instruction

load_dotenv()

# Audio Settings (Gemini ke requirements ke mutabik)
MIC_RATE   = 16_000   # Mic input rate (Gemini 16kHz standard PCM leta hai)
SPK_RATE   = 24_000   # Speaker output rate (Gemini 24kHz output deta hai)
CHUNK_SIZE = 320      # 20ms audio chunks (16000 * 0.02 = 320 frames)

async def run():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: .env file mein GOOGLE_API_KEY set karein!")
        return

    # PyAudio ko initialize karein aur streams open karein
    p = pyaudio.PyAudio()
    mic_stream = p.open(
        format=pyaudio.paInt16,
        channels=1, 
        rate=MIC_RATE, 
        input=True, 
        frames_per_buffer=CHUNK_SIZE
    )
    spk_stream = p.open(
        format=pyaudio.paInt16, 
        channels=1, 
        rate=SPK_RATE, 
        output=True
    )

    speaker_queue = asyncio.Queue()
    speaker_playing = False

    # Speaker loop: Speaker queue se audio lekar play karta hai
    async def play_loop():
        nonlocal speaker_playing
        while True:
            chunk = await speaker_queue.get()
            speaker_playing = True
            await asyncio.to_thread(spk_stream.write, chunk)
            speaker_queue.task_done()
            if speaker_queue.empty():
                speaker_playing = False

    # Gemini client aur configurations
    client = genai.Client(api_key=api_key)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Laomedeia")
            )
        ),
        system_instruction=types.Content(parts=[types.Part(text=instruction)]),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    print("\nJarvis (Gemini Live) se connect ho raha hai...")

    try:
        async with client.aio.live.connect(
            model="gemini-3.1-flash-live-preview", 
            config=config
        ) as session:
            print("Connected! Bolna shuru karein (Ctrl+C se band karein)\n")

            # Mic loop: Mic se data read karke Gemini ko bhejta hai
            async def send_loop():
                while True:
                    data = await asyncio.to_thread(mic_stream.read, CHUNK_SIZE, False)
                    # Agar Speaker active hai, toh mic data ko ignore karein (echo rokne ke liye)
                    if not speaker_playing:
                        await session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                        )

            # Receive loop: Gemini se responses aur transcripts leta hai
            async def receive_loop():
                user_buf, model_buf = [], []
                async for response in session.receive():
                    sc = response.server_content
                    if not sc:
                        continue

                    # Agar audio model response hai, toh use speaker queue me daalein
                    if sc.model_turn:
                        for part in sc.model_turn.parts or []:
                            if part.inline_data:
                                speaker_queue.put_nowait(part.inline_data.data)

                    # Transcriptions collect karein
                    if sc.input_transcription:
                        t = sc.input_transcription.text.strip()
                        if t: user_buf.append(t)

                    if sc.output_transcription:
                        t = sc.output_transcription.text.strip()
                        if t: model_buf.append(t)

                    # Turn complete hone par console par print karein
                    if sc.turn_complete:
                        if user_buf:
                            print(f"👤 User: {' '.join(user_buf)}")
                            user_buf.clear()
                        if model_buf:
                            print(f"🤖 Jarvis: {' '.join(model_buf)}")
                            model_buf.clear()

            # Sabhi loops ko parallel me run karein
            await asyncio.gather(play_loop(), send_loop(), receive_loop())

    finally:
        # Cleanup: Sabhi streams aur PyAudio ko properly close karein
        print("\nCleaning up audio resources...")
        mic_stream.stop_stream()
        mic_stream.close()
        spk_stream.stop_stream()
        spk_stream.close()
        p.terminate()
        print("Done!")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nJarvis Stopped. Alvida!")
