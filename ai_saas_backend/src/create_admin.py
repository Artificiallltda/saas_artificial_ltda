import uuid
from extensions import bcrypt, db
from models import User, Plan
from main import app
from utils import create_default_plans

with app.app_context():
    # Garante que os planos existem
    create_default_plans()

    # Tenta localizar admin pelo email
    existing_admin = User.query.filter_by(email="admin@example.com").first()
    if existing_admin:
        print("⚠️ Admin já existe:", existing_admin.username)
    else:
        # Busca o plano Pro
        pro_plan = Plan.query.filter_by(name="Pro").first()

        if not pro_plan:
            print("❌ Plano 'Pro' não encontrado. Verifique a seed de planos.")
        else:
            hashed_password = bcrypt.generate_password_hash("Admin123!").decode('utf-8')
            admin = User(
                id=str(uuid.uuid4()),
                full_name="Administrador",
                username="admin",
                email="admin@example.com",
                password=hashed_password,
                role="admin",
                is_active=True,
                plan_id=pro_plan.id  # ou plan=pro_plan
            )
            db.session.add(admin)
            db.session.commit()
            print("✅ Admin criado com sucesso com plano 'Pro'!")