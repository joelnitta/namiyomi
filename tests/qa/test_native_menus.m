#include "../../src/native_menus.m"
#include <assert.h>
#include <stdio.h>
int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
        NSMenu *menu=[[NSMenu alloc] initWithTitle:@"Edit"];
        NSMenuItem *copy=[[NSMenuItem alloc] initWithTitle:@"Copy Sequence" action:nil keyEquivalent:@""];
        NSMenuItem *emoji=[[NSMenuItem alloc] initWithTitle:@"Emoji & Symbols" action:@selector(orderFrontCharacterPalette:) keyEquivalent:@""];
        NSMenuItem *autofill=[[NSMenuItem alloc] initWithTitle:@"AutoFill" action:nil keyEquivalent:@""];
        [menu addItem:copy];[menu addItem:emoji];[menu addItem:autofill];
        for(int i=0;i<50;i++) {
            prune(menu);
            assert(menu.numberOfItems==3);
            assert([menu itemAtIndex:0]==copy && !copy.hidden);
            assert([menu itemAtIndex:1]==emoji && emoji.hidden);
            assert([menu itemAtIndex:2]==autofill && autofill.hidden);
        }
        puts("item count and order preserved; optional actions hidden");
    }
    return 0;
}
