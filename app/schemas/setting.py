from pydantic import BaseModel

class SettingBase(BaseModel):
    key: str
    value: str

class SettingCreate(SettingBase):
    pass

class Setting(SettingBase):
    class Config:
        from_attributes = True

class DirItem(BaseModel):
    name: str
    path: str
