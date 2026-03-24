require "active_support"
require "active_support/encrypted_file"

puts ActiveSupport::EncryptedFile.new(
  content_path: "credentials.yml.enc",
  key_path: "master.key",
  env_key: "RAILS_MASTER_KEY",
  raise_if_missing_key: true
).read
