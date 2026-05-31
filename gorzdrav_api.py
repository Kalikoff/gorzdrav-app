import aiohttp
from typing import Optional, List, Dict
from config import GORZDRAV_BASE

class GorzdravAPI:
    def __init__(self):
        self.base_url = GORZDRAV_BASE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://gorzdrav.spb.ru/service-free-schedule",
        }

    async def _get(self, url: str) -> Optional[Dict]:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
            except Exception as e:
                print(f"Request failed {url}: {e}")
                return None

    async def get_districts(self) -> List[Dict]:
        data = await self._get(f"{self.base_url}/shared/districts")
        return data.get("result", []) if data else []

    async def get_lpus_by_district(self, district_id: str) -> List[Dict]:
        data = await self._get(f"{self.base_url}/shared/district/{district_id}/lpus")
        return data.get("result", []) if data else []

    async def get_specialities(self, lpu_id: str) -> List[Dict]:
        data = await self._get(f"{self.base_url}/schedule/lpu/{lpu_id}/specialties")
        return data.get("result", []) if data else []

    async def get_doctors_list(self, lpu_id: str, speciality_id: str) -> List[Dict]:
        data = await self._get(f"{self.base_url}/schedule/lpu/{lpu_id}/speciality/{speciality_id}/doctors")
        if not data: return []
        
        normalized = []
        for doc in data.get("result", []):
            doc_name = doc.get('name') or doc.get('fullName') or "Врач"
            doc_id = doc.get('id')
            if doc_id:
                normalized_doc = dict(doc)
                normalized_doc['id'] = doc_id
                normalized_doc['fullName'] = doc_name
                normalized.append(normalized_doc)
        return normalized

    async def get_doctors_with_slots(self, lpu_id: str, speciality_id: str) -> List[Dict]:
        data = await self._get(f"{self.base_url}/schedule/lpu/{lpu_id}/speciality/{speciality_id}/doctors")
        if not data: return []
        
        available = []
        for doc in data.get("result", []):
            if doc.get("freeTicketCount", 0) > 0:
                normalized_doc = dict(doc)
                normalized_doc['id'] = doc.get('id')
                normalized_doc['fullName'] = doc.get('name') or doc.get('fullName') or "Врач"
                available.append(normalized_doc)
        return available

    async def get_timetable(self, lpu_id: str, doctor_id: str) -> List[Dict]:
        """Возвращает точное расписание врача по дням"""
        data = await self._get(f"{self.base_url}/schedule/lpu/{lpu_id}/doctor/{doctor_id}/timetable")
        return data.get("result", []) if data else []