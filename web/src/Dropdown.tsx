import * as Select from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import "./dropdown.css";

type DropdownProps = {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  placeholder?: string;
  className?: string;
  align?: "start" | "end";
};

export function Dropdown({
  label,
  value,
  onValueChange,
  children,
  placeholder,
  className = "",
  align = "start",
}: DropdownProps) {
  return (
    <Select.Root value={value} onValueChange={onValueChange}>
      <Select.Trigger className={"dropdown-trigger " + className} aria-label={label}>
        <Select.Value placeholder={placeholder} />
        <Select.Icon asChild>
          <ChevronDown className="dropdown-chevron" size={15} />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          className={"dropdown-menu " + className}
          aria-label={label}
          position="popper"
          align={align}
          sideOffset={6}
          collisionPadding={10}
        >
          <Select.Viewport className="dropdown-viewport">{children}</Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

export function DropdownGroup({label, children}: {label: string; children: ReactNode}) {
  return (
    <Select.Group className="dropdown-group">
      <Select.Label className="dropdown-label">{label}</Select.Label>
      {children}
    </Select.Group>
  );
}

export function DropdownOption({value, children}: {value: string; children: string}) {
  return (
    <Select.Item className="dropdown-option" value={value}>
      <Select.ItemText>{children}</Select.ItemText>
      <Select.ItemIndicator className="dropdown-check">
        <Check size={15} strokeWidth={2.4} />
      </Select.ItemIndicator>
    </Select.Item>
  );
}
