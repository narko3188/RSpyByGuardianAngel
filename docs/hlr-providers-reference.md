# SerbiaTracker — HLR Lookup Providers for +381 Serbia

## INFOBIP (Serbian Company, Belgrade)
# BEST OPTION for +381 — direct MNO relationships with Telekom Srbija, Yettel, A1

Endpoint: POST https://api.infobip.com/numberlookup/1/query
Auth: Authorization: App {API_KEY}
Body: {
    "to": ["+381641260161"],
    "ncNeeded": true,        # Number Context (MSC/VLR info)
    "hlrNeeded": true        # Force HLR lookup for MSC
}

Response fields (with ncNeeded+hlrNeeded):
- to.msisdn: phone number
- to.status.name: DELIVERED/UNDELIVERABLE/ABSENT
- to.originalNetwork.name: original operator
- to.originalNetwork.mncMnc: MCC+MNC
- to.servingMsc: SERVING MSC GLOBAL TITLE (location proxy)
- to.roaming: true/false if roaming
- to.ported: true/false if ported
- to.imsi: partial IMSI (first digits)
- to.error: error details if failed

Pricing: Enterprise (contact Infobip sales, Belgrade office)
Free trial: Possible — sign up at infobip.com, has test credit

## HLR-Lookups.com
Endpoint: GET https://api.hlr-lookups.com/v1/lookup?apikey={KEY}&msisdn={NUMBER}
Response: MSC GT, IMSI prefix, serving MSC, original network, ported, roaming
Pricing: ~$0.02-0.05 per lookup, credit-based

## Alternative: SMSGatewayHub
Endpoint: POST https://http-api.smsgatewayhub.com/api/mt/HLRLookup
Response: servingMSC, IMSI, mccMnc, ported, roaming

## For SERBIA-SPECIFIC: Infobip is the only local provider
# Their Belgrade HQ gives them direct SS7/SIGTRAN interconnects
# with all three Serbian MNOs. All other providers route through
# international carriers with higher latency and less detail.
