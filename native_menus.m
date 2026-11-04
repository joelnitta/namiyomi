// Hide optional system actions without changing AppKit/Tk menu indices.
// Never mutate item arrays from NSMenuDidAddItemNotification: AppKit may still
// be calculating insertion indices (including while Tk destroys its root).
#import <AppKit/AppKit.h>
static BOOL pruning=NO;
static void prune(NSMenu *menu) {
    if (!menu) return;
    // AppKit returns a snapshot array here; copying it leaks under non-ARC.
    for (NSMenuItem *item in menu.itemArray) {
        NSString *title=item.title.lowercaseString;
        NSString *action=NSStringFromSelector(item.action) ?: @"";
        if ([title containsString:@"autofill"] || [title containsString:@"auto fill"] ||
            [title containsString:@"dictation"] || [title containsString:@"emoji"] ||
            [title containsString:@"full screen"] || [action isEqualToString:@"toggleFullScreen:"] ||
            [action isEqualToString:@"startDictation:"] || [action isEqualToString:@"orderFrontCharacterPalette:"]) {
            item.hidden=YES;
        } else if (item.submenu) prune(item.submenu);
    }
}
void namiyomi_prune_menus(void) {
    if (pruning) return;
    pruning=YES;prune(NSApp.mainMenu);pruning=NO;
}
void namiyomi_configure_menus(void) {
    // registerDefaults is an in-process fallback domain, never a persistent write.
    [NSUserDefaults.standardUserDefaults registerDefaults:@{
        @"NSDisabledDictationMenuItem":@YES,@"NSDisabledCharacterPaletteMenuItem":@YES}];
    // Tk calls the non-destructive hide pass after idle and before posting its
    // menus. No synchronous native notification hooks or persistent defaults.
}
