import { createSignal, Show } from "solid-js";
import { KeyRound } from "lucide-solid";

import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";

import { SettingsLayout } from "./SettingsLayout";

// Import new form components
import {
  FormField,
  FormInput,
  FormButton,
  FormSuccess,
  FormCard,
  FormActions,
  type ValidationRule,
} from "@/components/forms";

export function SecurityPage() {
  const [oldPassword, setOldPassword] = createSignal("");
  const [newPassword, setNewPassword] = createSignal("");
  const [confirmPassword, setConfirmPassword] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [success, setSuccess] = createSignal(false);
  const [errors, setErrors] = createSignal<Record<string, string>>({});
  const { showToast } = useToast();

  const passwordValidation: ValidationRule = {
    validate: (value) => {
      if (!value) return true;
      if (value.length < 8) {
        return "Password must be at least 8 characters";
      }
      if (!/[A-Z]/.test(value)) {
        return "Password must contain at least one uppercase letter";
      }
      if (!/[a-z]/.test(value)) {
        return "Password must contain at least one lowercase letter";
      }
      if (!/[0-9]/.test(value)) {
        return "Password must contain at least one number";
      }
      return true;
    },
    message: "Password does not meet requirements",
  };

  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!oldPassword()) {
      newErrors.oldPassword = "Current password is required";
    }

    if (!newPassword()) {
      newErrors.newPassword = "New password is required";
    } else if (newPassword().length < 8) {
      newErrors.newPassword = "Password must be at least 8 characters";
    }

    if (!confirmPassword()) {
      newErrors.confirmPassword = "Please confirm your password";
    } else if (confirmPassword() !== newPassword()) {
      newErrors.confirmPassword = "Passwords do not match";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const submit = async (event: Event) => {
    event.preventDefault();

    if (!validateForm()) {
      return;
    }

    setLoading(true);
    try {
      await postJson("/change-password", {
        old_password: oldPassword(),
        new_password: newPassword(),
      });
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setErrors({});
      setSuccess(true);
      showToast("Password changed successfully.");
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to change password", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setErrors({});
  };

  return (
    <SettingsLayout
      title="Security"
      contentWidth="narrow"
    >
      <FormSuccess
        show={success()}
        message="Password changed successfully!"
        onDismiss={() => setSuccess(false)}
      />

      <FormCard
        title="Change Password"
        variant="elevated"
      >
        <form onSubmit={submit}>
          <FormField
            label="Current Password"
            required
            error={errors().oldPassword}
          >
            <FormInput
              value={oldPassword()}
              onChange={setOldPassword}
              type="password"
              placeholder="Enter current password"
              required
              showTogglePassword
            />
          </FormField>

          <FormField
            label="New Password"
            required
            error={errors().newPassword}
          >
            <FormInput
              value={newPassword()}
              onChange={setNewPassword}
              type="password"
              placeholder="Enter new password (min. 8 chars)"
              required
              minLength={8}
              validation={[passwordValidation]}
              showTogglePassword
              showValidationStatus
            />
          </FormField>

          <FormField
            label="Confirm New Password"
            required
            error={errors().confirmPassword}
          >
            <FormInput
              value={confirmPassword()}
              onChange={setConfirmPassword}
              type="password"
              placeholder="Confirm new password"
              required
              showTogglePassword
            />
          </FormField>

          <FormActions align="right">
            <FormButton
              variant="secondary"
              onClick={handleReset}
              disabled={loading()}
            >
              Reset
            </FormButton>
            <FormButton
              variant="primary"
              type="submit"
              loading={loading()}
              loadingText="Changing Password..."
            >
              Change Password
            </FormButton>
          </FormActions>
        </form>
      </FormCard>
    </SettingsLayout>
  );
}
