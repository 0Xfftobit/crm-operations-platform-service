"""SQL-backed repositories — same method surface as the in-memory ones, one short session per call.

Operational models round-trip through a JSON ``data`` blob (``model_dump`` ↔ ``Model(**)``), so the
DB schema stays stable as models evolve while key columns keep queries indexable.
"""
from app.db import engine
from app.db.orm import (
    AlertRow,
    CaseRow,
    ConsentRow,
    DisclosureRow,
    ExplainRow,
    PolicyRow,
    RegulationRow,
    RetentionRow,
    RuleChangeRow,
    RuleRow,
)
from app.domain.models import (
    Alert,
    Case,
    CompliancePolicy,
    ComplianceRule,
    Consent,
    DisclosureTimer,
    Regulation,
    RetentionRecord,
    RuleChange,
)


class SqlConsentRepository:
    def put(self, c: Consent) -> None:
        with engine.session() as s:
            s.merge(ConsentRow(lead_id=c.lead_id, channel=c.channel,
                               data=c.model_dump(mode="json")))
            s.commit()

    def get(self, lead_id: str, channel: str) -> Consent | None:
        with engine.session() as s:
            row = s.get(ConsentRow, (lead_id, channel))
            return Consent(**row.data) if row else None

    def list(self) -> list[Consent]:
        with engine.session() as s:
            return [Consent(**r.data) for r in s.query(ConsentRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ConsentRow).delete()
            s.commit()


class SqlAlertRepository:
    def add(self, a: Alert) -> None:
        with engine.session() as s:
            s.merge(AlertRow(k=a.alert_id, data=a.model_dump(mode="json")))
            s.commit()

    def list(self) -> list[Alert]:
        with engine.session() as s:
            return [Alert(**r.data) for r in s.query(AlertRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(AlertRow).delete()
            s.commit()


class SqlCaseRepository:
    def add(self, c: Case) -> None:
        with engine.session() as s:
            s.merge(CaseRow(k=c.case_id, data=c.model_dump(mode="json")))
            s.commit()

    def get(self, case_id: str) -> Case | None:
        with engine.session() as s:
            row = s.get(CaseRow, case_id)
            return Case(**row.data) if row else None

    def list(self) -> list[Case]:
        with engine.session() as s:
            return [Case(**r.data) for r in s.query(CaseRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(CaseRow).delete()
            s.commit()


class SqlDisclosureRepository:
    def put(self, t: DisclosureTimer) -> None:
        with engine.session() as s:
            s.merge(DisclosureRow(deal_id=t.deal_id, kind=t.kind,
                                  data=t.model_dump(mode="json")))
            s.commit()

    def get(self, deal_id: str, kind: str = "adverse_action") -> DisclosureTimer | None:
        with engine.session() as s:
            row = s.get(DisclosureRow, (deal_id, kind))
            return DisclosureTimer(**row.data) if row else None

    def list(self) -> list[DisclosureTimer]:
        with engine.session() as s:
            return [DisclosureTimer(**r.data) for r in s.query(DisclosureRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(DisclosureRow).delete()
            s.commit()


class SqlExplainabilityRepository:
    def put(self, ref: str, record: dict) -> None:
        with engine.session() as s:
            s.merge(ExplainRow(k=ref, data=record))
            s.commit()

    def get(self, ref: str) -> dict | None:
        with engine.session() as s:
            row = s.get(ExplainRow, ref)
            return row.data if row else None

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ExplainRow).delete()
            s.commit()


class SqlRuleRepository:
    def put(self, r: ComplianceRule) -> None:
        with engine.session() as s:
            s.merge(RuleRow(rule_id=r.rule_id, stage=r.stage, data=r.model_dump(mode="json")))
            s.commit()

    def get(self, rule_id: str, stage: str = "production") -> ComplianceRule | None:
        with engine.session() as s:
            row = s.get(RuleRow, (rule_id, stage))
            return ComplianceRule(**row.data) if row else None

    def list(self) -> list[ComplianceRule]:
        with engine.session() as s:
            return [ComplianceRule(**r.data) for r in s.query(RuleRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(RuleRow).delete()
            s.commit()


class SqlRuleChangeRepository:
    def add(self, c: RuleChange) -> None:
        with engine.session() as s:
            s.merge(RuleChangeRow(change_id=c.change_id, rule_id=c.rule_id,
                                  data=c.model_dump(mode="json")))
            s.commit()

    def get(self, change_id: str) -> RuleChange | None:
        with engine.session() as s:
            row = s.get(RuleChangeRow, change_id)
            return RuleChange(**row.data) if row else None

    def for_rule(self, rule_id: str) -> list[RuleChange]:
        with engine.session() as s:
            rows = s.query(RuleChangeRow).filter(RuleChangeRow.rule_id == rule_id).all()
            return sorted((RuleChange(**r.data) for r in rows), key=lambda c: c.new_version)

    def list(self) -> list[RuleChange]:
        with engine.session() as s:
            return [RuleChange(**r.data) for r in s.query(RuleChangeRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(RuleChangeRow).delete()
            s.commit()


class SqlRegulationRepository:
    def put(self, r: Regulation) -> None:
        with engine.session() as s:
            s.merge(RegulationRow(k=r.reg_id, data=r.model_dump(mode="json")))
            s.commit()

    def get(self, reg_id: str) -> Regulation | None:
        with engine.session() as s:
            row = s.get(RegulationRow, reg_id)
            return Regulation(**row.data) if row else None

    def list(self) -> list[Regulation]:
        with engine.session() as s:
            return [Regulation(**r.data) for r in s.query(RegulationRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(RegulationRow).delete()
            s.commit()


class SqlRetentionRepository:
    def put(self, r: RetentionRecord) -> None:
        with engine.session() as s:
            s.merge(RetentionRow(k=r.lead_id, data=r.model_dump(mode="json")))
            s.commit()

    def get(self, lead_id: str) -> RetentionRecord | None:
        with engine.session() as s:
            row = s.get(RetentionRow, lead_id)
            return RetentionRecord(**row.data) if row else None

    def list(self) -> list[RetentionRecord]:
        with engine.session() as s:
            return [RetentionRecord(**r.data) for r in s.query(RetentionRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(RetentionRow).delete()
            s.commit()


class SqlPolicyRepository:
    _KEY = "engine8-policy"

    def put(self, p: CompliancePolicy) -> bool:
        current = self.active()
        if current is not None and p.version < current.version:
            return False
        with engine.session() as s:
            s.merge(PolicyRow(k=self._KEY, data=p.model_dump(mode="json")))
            s.commit()
        return True

    def active(self) -> CompliancePolicy | None:
        with engine.session() as s:
            row = s.get(PolicyRow, self._KEY)
            return CompliancePolicy(**row.data) if row else None

    def clear(self) -> None:
        with engine.session() as s:
            s.query(PolicyRow).delete()
            s.commit()
