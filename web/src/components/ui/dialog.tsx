"use client";
import * as React from "react";
import * as Primitive from "@radix-ui/react-dialog";
import {X} from "lucide-react";
import {cn} from "@/lib/utils";
export const Dialog=Primitive.Root;
export const DialogTrigger=Primitive.Trigger;
export const DialogClose=Primitive.Close;
export const DialogTitle=Primitive.Title;
export const DialogDescription=Primitive.Description;
export function DialogHeader({className,...props}:React.ComponentProps<"div">){return <div className={cn("mb-4 space-y-2",className)} {...props}/>}
export function DialogFooter({className,...props}:React.ComponentProps<"div">){return <div className={cn("mt-5 flex flex-wrap justify-end gap-2",className)} {...props}/>}
export function DialogContent({className,children,...props}:React.ComponentProps<typeof Primitive.Content>){return <Primitive.Portal><Primitive.Overlay className="fixed inset-0 z-50 bg-black/40"/><Primitive.Content className={cn("fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border bg-background p-6 shadow-xl",className)} {...props}>{children}<Primitive.Close aria-label="关闭" className="absolute right-3 top-3 rounded p-1 focus-visible:ring-2 focus-visible:ring-ring"><X className="size-4"/></Primitive.Close></Primitive.Content></Primitive.Portal>}
