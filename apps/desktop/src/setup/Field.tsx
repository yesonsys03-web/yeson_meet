// === ANCHOR: FIELD_START ===
import { styles } from "./styles";

type FieldProps = {
  label: string;
  help: string;
  value: string;
  secret?: boolean;
  onChange: (value: string) => void;
  onBlur?: () => void;
};

// === ANCHOR: FIELD_FIELD_START ===
export function Field({ label, help, value, secret = false, onChange, onBlur }: FieldProps) {
  return (
    <label style={styles.field}>
      <span style={styles.label}>{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        onBlur={onBlur}
        style={styles.input}
      />
      <span style={styles.help}>{help}</span>
    </label>
  );
}
// === ANCHOR: FIELD_FIELD_END ===
// === ANCHOR: FIELD_END ===
