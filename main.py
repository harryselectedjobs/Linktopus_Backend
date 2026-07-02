from fastapi import FastAPI
from routers.auth_router import router as auth_router
import uvicorn
from fastapi.middleware.cors import CORSMiddleware


apps = FastAPI()

apps.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

apps.include_router(auth_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=1802)