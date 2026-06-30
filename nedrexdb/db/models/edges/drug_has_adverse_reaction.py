import datetime as _datetime

from pydantic import BaseModel as _BaseModel, StrictStr as _StrictStr, Field as _Field
from pymongo import UpdateOne as _UpdateOne

from nedrexdb.db import models


class DrugHasAdverseReactionBase(models.MongoMixin):
    edge_type: str = "DrugHasAdverseReaction"
    collection_name: str = "drug_has_adverse_reaction"

    @classmethod
    def set_indexes(cls, db):
        db[cls.collection_name].create_index("sourceDomainId")
        db[cls.collection_name].create_index("targetDomainId")
        db[cls.collection_name].create_index([("sourceDomainId", 1), ("targetDomainId", 1)], unique=True)


class DrugHasAdverseReaction(_BaseModel, DrugHasAdverseReactionBase):
    class Config:
        validate_assignment = True

    sourceDomainId: _StrictStr = ""
    targetDomainId: _StrictStr = ""
    report_count: int = 0
    serious_count: int = 0
    fatal_count: int = 0
    report_ids: list[str] = _Field(default_factory=list)
    countries: list[str] = _Field(default_factory=list)
    dataSources: list[str] = _Field(default_factory=list)

    def generate_update(self):
        tnow = _datetime.datetime.utcnow()

        query = {
            "sourceDomainId": self.sourceDomainId,
            "targetDomainId": self.targetDomainId,
        }

        update = {
            "$set": {
                "updated": tnow,
                "type": self.edge_type,
            },
            "$setOnInsert": {
                "created": tnow,
            },
            "$inc": {
                "report_count": self.report_count,
                "serious_count": self.serious_count,
                "fatal_count": self.fatal_count,
            },
            "$addToSet": {
                "report_ids": {"$each": self.report_ids},
                "countries": {"$each": self.countries},
                "dataSources": {"$each": self.dataSources},
            },
        }

        return _UpdateOne(query, update, upsert=True)