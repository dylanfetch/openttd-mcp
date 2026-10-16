class EpisodeHorizon extends GSInfo {
    function GetAuthor()      { return "openttd-mcp"; }
    function GetName()        { return "EpisodeHorizon"; }
    function GetShortName()   { return "EPHZ"; }
    function GetDescription() { return "Pauses the game permanently at the episode game-time horizon."; }
    function GetVersion()     { return 1; }
    function MinVersionToLoad() { return 1; }
    function GetAPIVersion()  { return "15"; }
    function GetDate()        { return "2026-08-30"; }
    function CreateInstance() { return "EpisodeHorizon"; }
}

RegisterGS(EpisodeHorizon());
