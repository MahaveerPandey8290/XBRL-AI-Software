# MCA business rules (C&I taxonomy 2016)

`Final_Business_Rule_CI_Taxonomy_2016_V1.3.xls` is MCA's official rule book for Non-Ind AS (Companies AS) AOC-4 XBRL filings.
`tools/extract_business_rules.py` turns it into `xbrl_aoc4/schema/business_rules_AS.json`, and `xbrl_aoc4/business_rules.py` applies it in the validator.
Every finding from these rules shows a reference such as *MCA BR 200100* (the ELR of the rule), *MCA BR line items #20* or *MCA BR generic rule 6*.

After a new version of the workbook is published, put it here and run:

```
python tools/extract_business_rules.py AS reference/mca/<workbook>.xls
python tools/build_web_zip.py
```

## What is checked

- **Element rules** (567 rules): mandatory (standalone / consolidated / current year only), mandatory for years starting after 01-04-2018, non-negative, greater than zero, not more than 100%, not after today, 18 years for dates of birth, valid PAN / CIN / DIN, country in MCA's list, presentation currency INR, unique values in a table, different from the company's own CIN / PAN, mandatory if Yes / greater than zero / not zero / entered (and vice versa), mandatory for a given member, CIN or PAN when the country is India, less than / greater than / equal to another element, tables mandatory always, on Yes, or when an amount is not zero.
- **Mandatory line items** for the 44 tables in the 'Mandatory Line Items' tab.
- **Generic rules**: 3 (parent member needed when a sub-member has amounts, with MCA's exemption lists), 4 (members 1, 2, 3 … in sequence), 5 (no images), 6 (previous year needs the current year and the other way round, except [201900] and the first year), 8 (at most 2 decimals), 10 (no dates before the year of incorporation in the CIN, with MCA's exceptions), 11, 13, 14 (sections that do not apply to consolidated or standalone documents), 12 (sections with no previous-year data).
- Rules already covered by the InstantXBRL template's own rule sheet, or by the totals and table-sum checks, are reported once.

## Checked only by MCA's servers (35 sentences)

These need MCA's or ICAI's / ICSI's databases (name matches CIN or DIN, director associated with the company, valid SRN, membership numbers), so the app cannot check them offline:

- [201600] `NameOfRelatedParty`: Name should be based on CINofrelatedparty, if entered
- [202300] `NameOfAmalgamatingCompany`: Name of the company should be based on CIN, if entered
- [202400] `NameOfAssociate`: Name should be based on CIN, if entered
- [202400] `NameOfAssociateForWhichReportingDateIsDifferent`: Name should be based on CIN, if entered
- [202400] `NameOfAssociateWhichIsYetToCommenceOperations`: Name should be based on 'CINOfAssociateWhichIsYetToCommenceOperations' if provided
- [202400] `NameOfAssociateLiquidatedOrSoldDuringYear`: Name should be based on 'CINOfAssociateLiquidatedOrSoldDuringYear'
- [202500] `NameOfJointlyControlledEntity`: Name should be based on CIN, if entered
- [202500] `NameOfJointVentureWhichIsYetToCommenceOperations`: Name should be based on 'CINOfJointVentureWhichIsYetToCommenceOperations', if provided
- [202500] `NameOfJointVentureLiquidatedOrSoldDuringYear`: Name should be based on 'CINOfJointVentureLiquidatedOrSoldDuringYear', if provided
- [202800] `NameOfSubsidiary`: Name should be based on 'CINOfSubsidiaryCompany' if entered
- [202800] `SRNOfFilingOfBalanceSheetBySubsidiary`: Valid SRN of Form 23 AC/ACA, 23AC/ACA XBRL,AOC-4 or AOC-4 XBRL. Should belong to CIN of subsidiary company
- [202800] `NameOfSubsidiaryWhichIsYetToCommenceOperations`: Name should be based on 'CINOfSubsidiaryCompanyWhichIsYetToCommenceOperations', if provided
- [202800] `NameOfSubsidiaryLiquidatedOrSoldDuringYear`: Name should be based on 'CINOfSubsidiaryCompanyLiquidatedOrSoldDuringYear'
- [300700] `DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable`: Director should be associated with the company at any time during the PeriodCoveredByFinancialStatements
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: If value of ‘DesignationOfKeyManagerialPersonnelOrDirector’ is one of the below then Name should be based on DIN in any order:
- [300700] `PermanentAccountNumberOfKeyManagerialPersonnelOrDirector`: PAN should be associated with Company
- [300700] `DesignationOfKeyManagerialPersonnelOrDirector`: KMP should be associated with the company during the reporting period
- [400100] `NameOfCompany`: Name should be based on CIN
- [400100] `CorporateIdentityNumber`: Valid CIN. Should be same as entered in the Form
- [400100] `DateOfStartOfReportingPeriod`: Should be same as entered in the Form for the current year
- [400100] `DateOfEndOfReportingPeriod`: Should be same as entered in the Form for the current year
- [400200] `NameOfAuditorSigningReport`: Should be as per ICAI database based on MembershipNumberOfAuditor in any order
- [400200] `MembershipNumberOfAuditor`: Should be a valid membership number as per ICAI database
- [400200] `SRNOfFormADT1`: Should be valid SRN of Form 23B/GNL-2/ADT-1and should be associated with the company or should be Z99999999
- [400200] `SRNOfFormADT1`: SRN of Form 23B/GNL-2/ADT-1as entered should not have been marked as ‘Defective’
- [400300] `DetailsOfDirectorsSigningFinancialStatementsTable`: Director should be associated with the company as on date of signing of Balance sheet by that director
- [400300] `NameOfDirectorSigningFinancialStatementsAbstract`: Name should be based on DIN in any order
- [400300] `DetailsOfCompanySecretarySigningFinancialStatementsAbstract`: Company Secretary should be associated with the company as on date of signing of balance sheet
- [400300] `PermanentAccountNumberOfChiefFinancialOfficer`: Should be associated with the company on the date of signing balance sheet
- [400300] `DetailsOfManagerSigningFinancialStatementsLineItems`: Manager should be associated with the company as on date of signing of balance sheet by that manager
- [400400] `DetailsOfDirectorsSigningBoardReportTable`: Director should be associated with the company as on date of signing of Board Report by that director
- [400400] `NameOfDirectorSigningBoardReportAbstract`: Name should be based on DIN in any order
- [400500] `NameOfSecretarialAuditorSigningReport`: Should be as per ICSI database based on 'MembershipNumberOfSecretarialAuditor' in any order
- [400500] `MembershipNumberOfSecretarialAuditor`: Should be a valid membership number as per ICSI database
- [400500] `CertificateOfPracticeNumberOfSecretarialAuditor`: Should be a valid COP number as per ICSI database

## Other sentences not turned into an automatic check (111)

Most of these are sums between a table and the balance sheet, which `Validator.check_mca` and the calculation checks already test (share capital, reserves, borrowings, investments, provisions, loans and advances, trade receivables, tangible and intangible assets, CSR amounts). The rest are listed for review:

- [100100] `ShareCapital`: Should be less than or equal to "Value of shares subscribed - equity + Preference"
- [100100] `ShareCapital`: Should be equal to zero in case of company not having share capital
- [100100] `ShareCapital`: In case of section 8 company- If the company not having share capital then it should always zero
- [100100] `ShareCapital`: But If the company having share capital then it should always be greater than zero
- [100100] `ShareCapital`: Status of the company (Pvt/Public/Section 8) to be checked on the Date of end of reporting period
- [200100] `ValueOfSharesAuthorised`: Should be equal to 'NumberOfSharesAuthorised' multiplied by 'ParValuePerShare'
- [200100] `ValueOfSharesIssued`: Should be equal to 'NumberOfSharesIssued' multiplied by 'ParValuePerShare'
- [200100] `ValueOfSharesPaidUp`: Summation of 'Value of Shares paid up' for 'EquitySharesMembers 1..10' and 'PreferenceSharesMembers 1...10' should be equal to value entered in 'Sharecapital' in balance sheet
- [200100] `ShareCapital`: Summation of 'ShareCapital' (Closing)' for 'EquitySharesMembers 1..10' and 'PreferenceSharesMembers 1...10' should be equal to value entered in 'Sharecapital' in balance sheet
- [200100] `CINOfShareholder`: Either CIN of shareholder or PAN of shareholder is Mandatory in case Country of incorporation or residence of shareholder is India
- [200100] `NumberOfSharesHeldInCompany`: Summation of all shareholder members should be less than or equal to summation of 'Number of Shares Paid Up' for 'EquitySharesMembers 1..10' and 'PreferenceSharesMembers 1...10' in DisclosureOfClassesOfShareCapital
- [200100] `NumberOfPersonsOnPrivatePlacementOfEquityShare`: Either this element or NumberOfPersonsOnPrivatePlacementOfPreferenceShare is mandatory if 'NumberOfSharesIssuedInPrivatePlacement' is greater than zero
- [200100] `WhetherMoneyRaisedFromPublicOfferingDuringYear`: YES should be selected in case amount entered in 'AmountOfPublicIssueDuringPeriod' is greater than zero
- [200200] `StatementOfChangesInReservesTable`: Should be mandatory if ReservesAndSurplus in balance sheet is other than zero
- [200200] `Reserves`: Total Value of Reserves- (Closing Balance) should be equal to value entered in 'ReservesAndSurplus' in balance sheet
- [200200] `Reserves`: To validate this, in ComponentsOfReservesAxis- Sum of First level child members shall be considered (CapitalReservesMember, CapitalRedemptionReservesMember etc..)
- [200200] `Reserves`: However, in case the first level child is not given and its corresponding lower level child members are given then the same shall be considered
- [200300] `Borrowings`: Summation of various Borrowings members for Long term period should be equal to 'LongTermBorrowings' in financial statements
- [200300] `Borrowings`: Summation of various Borrowings members for Short term period should be equal to 'ShortTermBorrowings' in financial statements
- [200300] `Borrowings`: To validate this, in ClassificationOfBorrowingsAxis- Only First level child members shall be considered (BondsDebenturesMember, TermLoansMember etc..)
- [200300] `Borrowings`: However, in case the first level child is not given and its corresponding lower level child members are given then the same shall be considered
- [200300] `OutstandingAmountOfContinuingDefaultPrincipal`: Mandatory in case Beginning Date of continuing Default is entered
- [200300] `OutstandingAmountOfContinuingDefaultInterest`: Mandatory in case Beginning Date of continuing Default is entered
- [200300] `DetailsOfBondsOrDebenturesTable`: This table should be mandatory in case details are entered for 'BondsMember or DebenturesMember' under 'Classification of borrowings [Axis]' and vice-a-versa
- [200400] `NoncurrentInvestments`: Summation of 'NoncurrentInvestments' for all dimensional members should be equal to 'NonCurrentInvestments' in Financial statements
- [200400] `AggregateAmountOfQuotedNoncurrentInvestments`: Summation of 'AggregateAmountOfQuotedNoncurrentInvestments' and 'AggregateAmountOfUnquotedNoncurrentInvestments' should be equal to 'NoncurrentInvestments' in financial statements
- [200500] `CurrentInvestments`: Summation of 'CurrentInvestments' for all dimensional members should be equal to 'CurrentInvestments' in Balance sheet
- [200600] `Provisions`: Summation of all Provisions - Long term member and Short term member should be equal to 'LongTermProvisions' and 'ShortTermProvisions' respectively in financial statements
- [200600] `LoansAndAdvancesTable`: This table is mandatory in case amount entered in 'LongtermLoandandAdvances' or 'ShortTermLoandandAdvances' is other than zero in financial statements
- [200600] `LoansAndAdvances`: Summation of all LoansAndAdvances - Long term member and Short term member should be equal to 'LongTermLoansAndAdvances' and 'ShortTermLoansAndAdvances' respectively in financial statements
- [200600] `LoansAndAdvances`: However, in case the first level child is not given and its corresponding lower level child members are given then the same shall be considered
- [200600] `LoansAndAdvances`: For validating this, only first level child members shall be considered in ClassificationOfLoansAndAdvancesAxis- . (For eg: CapitalAdvancesMember, SecurityDepositsMember etc.)
- [200600] `SubclassificationOfTradeReceivablesTable`: This table is mandatory if 'TrdaeReceivable' is other than zero in financial statements
- [200600] `TradeReceivables`: Summation of TradeReceivables - SecuredConsideredGoodMember; UnsecuredConsideredGoodMember; DoubtfulMember should be equal to 'TradeReceivables' in financial statements
- [201000] `TangibleAssets`: Value in CarryingAmountMember should be equal to 'GrossCarryingAmountMember' less 'AccumulatedDepreciationAndImpairmentMember'
- [201000] `ChangesInTangibleAssets`: Value in CarryingAmountMember should be equal to 'GrossCarryingAmountMember' less 'AccumulatedDepreciationAndImpairmentMember'
- [201000] `TangibleAssets`: Summation of CarryingAmountMember for the following first level child members in ClassesOfTangibleAssetsAxis should be equal to 'TangibleAssets' in Balance sheet:
- [201000] `TangibleAssets`: LandMember
- [201000] `TangibleAssets`: BuildingsMember
- [201000] `TangibleAssets`: PlantAndEquipmentMember
- [201000] `TangibleAssets`: FurnitureAndFixturesMember
- [201000] `TangibleAssets`: VehiclesMember
- [201000] `TangibleAssets`: OfficeEquipmentMember
- [201000] `TangibleAssets`: ComputerEquipmentMember
- [201000] `TangibleAssets`: LeaseholdImprovementsMember
- [201000] `TangibleAssets`: OtherTangibleAssetsMember
- [201100] `IntangibleAssets`: Value in CarryingAmountMember should be equal to 'GrossCarryingAmountMember' less 'AccumulatedAmortizationAndImpairmentMember'
- [201100] `ChangesInIntangibleAssets`: Value in CarryingAmountMember should be equal to 'GrossCarryingAmountMember' less 'AccumulatedAmortizationAndImpairmentMember'
- [201100] `IntangibleAssets`: Summation of CarryingAmountMember for the following members in ClassesOf IntangibleAssetsAxis should be equal to 'IntangibleAssets' in Balance sheet:
- [201100] `IntangibleAssets`: GoodwillMember
- [201100] `IntangibleAssets`: BrandsAndTradeMarksMember
- [201100] `IntangibleAssets`: ComputerSoftwareMember
- [201100] `IntangibleAssets`: MastheadsAndPublishingTitlesMember
- [201100] `IntangibleAssets`: MiningRightsMember
- [201100] `IntangibleAssets`: CopyrightsPatentsAndOtherOperatingRightsMember
- [201100] `IntangibleAssets`: RecipesFormulaeModelsDesignsAndPrototypesMember
- [201100] `IntangibleAssets`: LicensesAndFranchiseMember
- [201100] `IntangibleAssets`: OtherIntangibleAssetsMember
- [201600] `DescriptionOfNatureOfRelatedPartyRelationship`: (Holding company) or (Ultimate Holding company) should be selected in atleast one dimensional member in case Yes is selected in 'WhetherCompanyIsSubsidiaryCompany' and vice-a-versa
- [202400] `PANOfAssociateEntity`: Either PANOfassociate' or 'CINOfassociate' is mandatory if country is India
- [202500] `PANOfJointVenturer`: Either PANOfjointventure' or 'CINOfjointventure' is mandatory if country is India
- [202600] `DisclosureOfDetailsOfEntitiesConsolidatedTable`: Should be mandatory if 'WhetherConsolidatedFinancialStatementsIsApplicableOnCompany' is Yes
- [202800] `WhetherCompanyHasSubsidiaryCompanies`: Subsidiary details can be entered only if yes is selected in this field
- [202800] `StartDateOfAccountingPeriodOfSubsidiary`: This can be less than the date of incorporation of the filing company
- [202800] `EndDateOfAccountingPeriodOfSubsidiary`: This can be less than the date of incorporation of the filing company
- [202800] `ReportingCurrencyOfSubsidiary`: Should be valid currency code as per the list of currency codes
- [300600] `StartDateOfAccountingPeriodOfPartnershipFirmOrAssociationOfPersonsOrLimitedLiabilityPartnershipsInRespectOfWhichProfitOrLossesHaveBeenAccountedInCompanysBooksInCaseWhereYearEndingIsDifferentForPartnershipFirmOrAssociationOfPersonsOrLimitedLiabilityPartnershipsAndCompany`: This can be less than the date of incorporation of the filing company
- [300600] `EndDateOfAccountingPeriodOfPartnershipFirmOrAssociationOfPersonsOrLimitedLiabilityPartnershipsInRespectOfWhichProfitOrLossesHaveBeenAccountedInCompanysBooksInCaseWhereYearEndingIsDifferentForPartnershipFirmOrAssociationOfPersonsOrLimitedLiabilityPartnershipsAndCompany`: This can be less than the date of incorporation of the filing company
- [300700] `DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable`: Mandatory to enter details of atleast 1 in case of OPC, 2 in case of private company, 3 in case of public company and 5 in case of Producer company
- [300700] `DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable`: Status of the company (Private/Public/Producer/OPC) to be checked as on the Date of end of reporting period
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Managing Director)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Whole Time Director)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Independent Director)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Other Non Executive Director)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: If value of ‘DesignationOfKeyManagerialPersonnelOrDirector’ is one of the below then Name should be based on PAN details available:
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Manager)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (CEO)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (Company Secretary)
- [300700] `NameOfKeyManagerialPersonnelOrDirector`: • (CFO)
- [301000] `FinancialYearMember`: Details of atleast year one member will be mandatory if Disclosure of net profits for last three financial years [Table] becomes mandatory
- [301000] `ProfitBeforeTaxOfFinancialYear`: Value entered in this field Financial Year Member 1, should match with the value entered in the ELR “100200” for the element “ProfitBeforeTax” for the previous reporting period
- [301000] `AverageNetProfitForLastThreeFinancialYears`: Value entered here should be equivalent to average of values entered in "NetProfitComputedUnderSection198AndAdjustedAsPerRule21FOfCompaniesCSRPolicyRules2014"
- [301000] `PrescribedCSRExpenditure`: Value entered here should be 2 percent of "AverageNetProfitForLastThreeFinancialYears" if "AverageNetProfitForLastThreeFinancialYears" is greater than zero
- [301000] `AmountCSRSpentForFinancialYear`: The amount entered here should be same as "CSRExpenditure" in ELR 100200 of 'Statement of profit and loss"
- [301000] `AmountCSRSpentForFinancialYear`: The amount entered here should be same as "CSRExpenditure" in ELR 100200 of 'Statement of profit and loss"
- [301000] `AmountCSRSpentForFinancialYear`: AND
- [301000] `AmountCSRSpentForFinancialYear`: It should match with the total of CSR spending amount in the 'ClassificationOfCSRSpendingTable' table element 'AmountSpentOnProjectsOrPrograms'
- [301000] `NameOfStateOrUnionTerritoryWhereProjectsOrProgramsWasUndertaken`: This field is mandtory if 'SectorInWhichProjectIsCovered' is entered
- [301000] `DetailsOfImplementingAgency`: Shall be mandatory if ModeOfAmountSpent is other than "directly by company"
- [400100] `TypeOfIndustry`: Only Commercial and Industrial can be selected for the time being
- [400100] `DateOfEndOfReportingPeriod`: Difference between start date and end date should not be greater than 18 months
- [400100] `DateOfEndOfReportingPeriod`: In case of previous year, date entered in this field should be one day less than date entered in field 'DateOfStartOfReportingPeriod' for current year.'
- [400100] `TypeOfCashFlowStatement`: Elements for cash flow- direct or indirect shall be applicable based on the value selected in field Type of cash flow
- [400100] `TypeOfCashFlowStatement`: For eg: In case type of Cash flow is direct, only elements in respect of direct method should be there and vice-a-versa
- [400100] `InternetProtocolAddressOfServiceProvider`: Mandatory to enter only if name of the service provider entered
- [400100] `LocationOfTheServiceProvider`: Mandatory to enter only if name of the service provide is entered
- [400100] `WhetherBooksOfAccountAndOtherBooksAndPapersAreMaintainedOnCloud`: Mandatory to enter only if name of the service provide is entered
- [400100] `AddressAsProvidedByTheServiceProvider`: Mandatory to enter only if Yes is selected in “where the books of account and other books and papers are maintained on cloud-
- [400100] `ProductOrServiceCategoryITC4DigitCode`: Should be a valid 4 digit Product/Service ITC/NPCS code
- [400100] `HighestTurnoverContributingProductOrServiceITC8DigitCode`: Should be a valid 8 digit Product/Service code corresponding to the ITC/NPCS 4 digit code
- [400200] `DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportLineItems`: In case a line item is provided for any member then the same line item cannot be provided against any other member
- [400200] `DetailsRegardingAuditorsTable`: All Details (Except name of audit firm) of atleast one auditor to be mandatory
- [400200] `SRNOfFormADT1`: Z99999999 should be entered only in case filing of Form ADT-1, 23B or GNL-2 is not applicable for auditor
- [400200] `DateOfSigningOfBalanceSheetByAuditors`: Should be greater than or equal to Date of signing balance sheet by director or secretary or manager
- [400200] `DateOfSigningOfBalanceSheetByAuditors`: In case of multiple dates, latest date should be considered
- [400300] `DetailsOfDirectorsSigningFinancialStatementsTable`: All Details of atleast one director should be entered
- [400400] `DetailsOfDirectorsSigningBoardReportTable`: All Details of atleast one director should be entered
- [400400] `DetailsOfContractsArrangementsTransactionsNotAtArmsLengthBasisTable`: This table is mandatory if Yes is selected in “WheterThereAreContractsArrangementsTransactionsNotAtArmsLength”
- [400400] `DetailsOfMaterialContractsArrangementsTransactionsAtArmsLengthBasisTable`: This table is mandatory if Yes is selected in “WhetherThereAreContractsArrangementsTransactionsAtArmslengthbasis”
- [400500] `NameOfSecretarialAuditFirm`: Mandatory if 'Secretarial auditors firm' is selected in 'CategoryOfSecreterialAuditor'
- [400500] `FirmsRegistrationNumberOfSecretarialAuditFirm`: Mandatory if 'Secretarial auditors firm' is selected in 'CategoryOfSecreterialAuditor'
