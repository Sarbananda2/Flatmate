/**
 * The first visit to a new installation: whoever signs up here becomes the
 * household admin, signed in at once and sent on to the guided setup.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Primitives'
import { TextField } from '../../components/Form'
import { BUTTON_PRIMARY } from '../../components/ui'
import { useAuth } from '../../context/AuthContext'
import { AuthCard, EMAIL_PATTERN, MIN_PASSWORD_LENGTH } from './AuthCard'

export default function SignupAdminPage() {
  const { signupAdmin } = useAuth()
  const navigate = useNavigate()
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const errors = {
    fullName: fullName.trim() ? null : 'Enter your full name.',
    email: !email.trim()
      ? 'Enter your email address.'
      : EMAIL_PATTERN.test(email.trim())
        ? null
        : 'Enter a valid email address, like name@example.com.',
    password:
      password.length >= MIN_PASSWORD_LENGTH
        ? null
        : `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
  }
  const valid = !errors.fullName && !errors.email && !errors.password

  return (
    <AuthCard
      title="Create your Household Admin Account"
      subtitle="Set up your account to manage your apartment, residents, and shared expenses."
    >
      <form
        className="stack"
        noValidate
        onSubmit={async (event) => {
          event.preventDefault()
          setSubmitted(true)
          if (!valid || pending) return
          setPending(true)
          setError(null)
          try {
            await signupAdmin({ fullName, email, password })
            navigate('/welcome', { replace: true })
          } catch (cause) {
            setError(cause instanceof ApiError ? cause : null)
            setPending(false)
          }
        }}
      >
        {error && <ErrorNotice error={error} />}
        <TextField
          label="Full name"
          value={fullName}
          onChange={setFullName}
          required
          maxLength={200}
          placeholder="e.g. Sarbananda Taw"
          autoComplete="name"
          error={submitted ? errors.fullName : null}
        />
        <TextField
          label="Email address"
          type="email"
          value={email}
          onChange={setEmail}
          required
          maxLength={254}
          placeholder="you@example.com"
          autoComplete="email"
          error={submitted ? errors.email : null}
        />
        <TextField
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          required
          maxLength={1024}
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          error={submitted ? errors.password : null}
        />
        <button type="submit" className={`${BUTTON_PRIMARY} w-full justify-center`} disabled={pending}>
          {pending ? 'Creating your account…' : 'Create account'}
        </button>
      </form>
    </AuthCard>
  )
}
