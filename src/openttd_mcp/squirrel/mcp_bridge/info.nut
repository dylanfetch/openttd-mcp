class McpBridge extends AIInfo {
    function GetAuthor()      { return "openttd-mcp"; }
    function GetName()        { return "McpBridge"; }
    function GetShortName()   { return "MCPB"; }
    function GetDescription() { return "Model Context Protocol bridge for OpenTTD."; }
    function GetVersion()     { return 1; }
    function MinVersionToLoad() { return 1; }
    function GetAPIVersion()  { return "15"; }
    function GetDate()        { return "2026-06-27"; }
    function CreateInstance() { return "McpBridge"; }
    function UseAsRandomAI()  { return false; }
}

RegisterAI(McpBridge());

// Savegames identify an AI by GetName(). Keep this metadata-only registration
// so saves made before the bridge rename load the current McpBridge class.
class LegacyBridgeInfo extends McpBridge {
    function GetName()        { return "BusRouteBridge"; }
    function GetShortName()   { return "BRTE"; }
    function GetDescription() { return "Compatibility registration for saved games."; }
    function GetDate()        { return "2026-08-29"; }
}

RegisterAI(LegacyBridgeInfo());
