import os
import uuid
import time
from io import BytesIO
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from extensions import db
from models.generated_content import GeneratedVideoContent
from models.user import User
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client_gemini = genai.Client(api_key=GEMINI_API_KEY)

ai_generation_video_api = Blueprint("ai_generation_video_api", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "static", "uploads")
VIDEO_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "videos")
os.makedirs(VIDEO_UPLOAD_DIR, exist_ok=True)


def _candidate_models(model_used: str):
    models = [model_used]
    # Fallbacks sugeridos: 3.1 (preview) -> 3.0 fast -> 3.0
    if model_used.startswith("veo-3.1"):
        if "fast" in model_used:
            models.append("veo-3.0-fast-generate-001")
            models.append("veo-3.0-generate-001")
        else:
            models.append("veo-3.0-generate-001")
            models.append("veo-3.0-fast-generate-001")
    # fallback geral adicional
    if "veo-3.0-fast-generate-001" not in models:
        models.append("veo-3.0-fast-generate-001")
    if "veo-3.0-generate-001" not in models:
        models.append("veo-3.0-generate-001")
    return models


@ai_generation_video_api.route("/generate-video", methods=["POST"])
@jwt_required()
def generate_video():
    current_user_id = get_jwt_identity()
    user = User.query.get(current_user_id)
    if not user:
        return jsonify({"error": "Usuário inválido"}), 404

    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    model_used = data.get("model_used", "veo-3.0-fast-generate-001")
    aspect_ratio = data.get("ratio", "16:9")

    if not prompt:
        return jsonify({"error": "Campo 'prompt' é obrigatório"}), 400

    try:
        filename = f"{uuid.uuid4()}.mp4"
        save_path = os.path.join(VIDEO_UPLOAD_DIR, filename)
        print(f"[DEBUG] Gerando vídeo com modelo {model_used}, ratio {aspect_ratio}...")

        # Tenta modelo preferido + fallbacks
        candidates = _candidate_models(model_used)
        operation = None
        last_err_text = ""
        saw_quota_error = False
        saw_not_found = False

        for m in candidates:
            try:
                print(f"[DEBUG] Tentando modelo {m}...")
                operation = client_gemini.models.generate_videos(
                    model=m,
                    prompt=prompt,
                    config=types.GenerateVideosConfig(aspect_ratio=aspect_ratio)
                )
                model_used = m  # efetivamente usado
                break
            except Exception as ex:
                err_text = str(ex)
                last_err_text = err_text
                # 429 / quota
                if "RESOURCE_EXHAUSTED" in err_text or "rate-limit" in err_text or "429" in err_text:
                    saw_quota_error = True
                    # tenta próximo candidato; se todos falharem, retornaremos 429 amigável
                    continue
                # 404 / modelo não encontrado/indisponível para a conta
                if "NOT_FOUND" in err_text or "is not found" in err_text:
                    saw_not_found = True
                    continue
                # outros erros: propague
                raise

        if operation is None:
            if saw_quota_error:
                return jsonify({"error": "Limite de uso da API atingido. Tente novamente mais tarde."}), 429
            if saw_not_found:
                return jsonify({"error": "Modelo indisponível para esta conta/região."}), 404
            return jsonify({"error": last_err_text or "Falha ao iniciar geração de vídeo"}), 500

        # Aguarda conclusão da operação
        while not operation.done:
            time.sleep(5)
            operation = client_gemini.operations.get(operation)

        generated_video = operation.response.generated_videos[0]
        video_bytes = client_gemini.files.download(file=generated_video.video)

        # Salva o arquivo localmente
        with open(save_path, "wb") as f:
            f.write(video_bytes)

        # Salva no banco
        video_entry = GeneratedVideoContent(
            user_id=user.id,
            prompt=prompt,
            model_used=model_used,
            file_path=save_path,
            created_at=datetime.utcnow(),
        )
        db.session.add(video_entry)
        db.session.commit()

        return jsonify({
            "message": "Vídeo gerado com sucesso!",
            "video": video_entry.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        msg = str(e)
        # Mensagem amigável quando quota estoura
        if "RESOURCE_EXHAUSTED" in msg or "rate-limit" in msg or "429" in msg:
            return jsonify({"error": "Limite de uso da API atingido. Tente novamente mais tarde."}), 429
        if "NOT_FOUND" in msg or "is not found" in msg:
            return jsonify({"error": "Modelo indisponível para esta conta/região."}), 404
        print("Erro ao gerar vídeo:", msg)
        return jsonify({"error": msg}), 500