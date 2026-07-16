from fastapi import FastAPI
from routers.auth_router import router as auth_router
from routers.share_post_router import router as share_post_router
from routers.webhooks import router as wenhook_router
from routers.linkedin_recruiter_automation_router import router as automation_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(share_post_router)
app.include_router(wenhook_router)
app.include_router(automation_router)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=1802)