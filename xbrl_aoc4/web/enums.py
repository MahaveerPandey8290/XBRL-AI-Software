"""Fallback drop-down values for enumerated MCA data types (the template's own
lists, captured in the schema as item['enum'], take precedence)."""
ENUMS = {
    "NatureOfReport": ["Standalone", "Consolidated"],
    "ContentOfReport": ["Financial Statements"],
    "LevelOfRounding": ["Actual", "Thousands", "Lakhs", "Millions", "Crores", "Billions"],
    "TypeOfCashFlowStatement": ["Direct Method", "Indirect Method"],
    "IndustryType": ["Commercial and Industrial", "Bank", "NBFC", "Power", "Insurance"],
    "CategoryOfAuditor": ["Individual", "Auditors firm"],
}
